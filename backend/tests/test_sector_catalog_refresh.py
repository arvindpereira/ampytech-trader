"""Tests for portfolio holdings aggregation and sector catalog refresh."""
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from ml_engine.portfolio_holdings import all_holdings, portfolio_tickers
from data_ingestion.sector_catalog_refresh import _canonical_sector, refresh_catalog
from data_ingestion.ticker_metadata_fetcher import is_equity_ticker


class _Lot:
    def __init__(self, ticker, account=None, shares=10, lot_type="rsu"):
        self.ticker = ticker
        self.account_label = account
        self.shares = shares
        self.lot_type = lot_type


class _Ext:
    def __init__(self, ticker, account, shares=5):
        self.ticker = ticker
        self.account_label = account
        self.shares = shares
        self.statement_date = "2026-06-01"
        self.source = "manual"


class _Pos:
    def __init__(self, ticker, qty):
        self.ticker = ticker
        self.quantity = qty
        self.mode = "virtual"


class PortfolioHoldingsTests(unittest.TestCase):
    def test_collects_internal_and_external(self):
        db = MagicMock()

        def query_side(model):
            q = MagicMock()
            name = getattr(model, "__name__", str(model))
            if name == "EquityLot":
                q.all.return_value = [_Lot("ADBE", "work")]
            elif name == "VirtualPosition":
                q.filter.return_value = q
                q.all.return_value = [_Pos("NVDA", 3)]
            elif name == "ExternalStatementHolding":
                q.all.return_value = [_Ext("AAPL", "Robinhood")]
            elif name == "ResearchWatchlist":
                q.all.return_value = []
            else:
                q.all.return_value = []
            return q

        db.query.side_effect = query_side
        tickers = portfolio_tickers(db)
        self.assertEqual(set(tickers), {"ADBE", "NVDA", "AAPL"})
        holds = all_holdings(db)
        sources = {h["source"] for h in holds}
        self.assertIn("equity_lot", sources)
        self.assertTrue(any(s.startswith("external:") for s in sources))


class SectorCatalogRefreshTests(unittest.TestCase):
    def test_canonical_sector_aliases(self):
        from ml_engine.sector_resolver import canonical_sector

        self.assertEqual(canonical_sector("Consumer Staples"), "Consumer Defensive")
        self.assertEqual(canonical_sector("Information Technology"), "Technology")
        self.assertEqual(canonical_sector("Technology"), "Technology")
        self.assertEqual(canonical_sector("Retail"), "Consumer Cyclical")

    def test_skip_etfs_for_seeds(self):
        self.assertFalse(is_equity_ticker("XLK"))
        self.assertTrue(is_equity_ticker("NVDA"))

    @patch("data_ingestion.sector_catalog_refresh.refresh_tickers")
    @patch("data_ingestion.sector_catalog_refresh.portfolio_tickers")
    @patch("data_ingestion.sector_catalog_refresh.all_holdings")
    def test_refresh_ranks_by_market_cap(self, mock_holds, mock_port, mock_fetch):
        mock_fetch.return_value = {"updated": 2}
        mock_port.return_value = ["NVDA", "JPM"]
        mock_holds.return_value = [
            {"ticker": "NVDA", "source": "virtual", "account": None},
            {"ticker": "JPM", "source": "external:ira", "account": "ira"},
        ]

        db = MagicMock()

        class Meta:
            def __init__(self, ticker, sector, cap, industry=None):
                self.ticker = ticker
                self.sector = sector
                self.market_cap = cap
                self.industry = industry

        meta_rows = [
            Meta("NVDA", "Technology", 3e12, "Semiconductors"),
            Meta("AMD", "Technology", 2e11, "Semiconductors"),
            Meta("MSFT", "Technology", 2.8e12, "Software"),
            Meta("JPM", "Financial Services", 5e11, "Banks"),
        ]

        def query_side(model):
            q = MagicMock()
            if model.__name__ == "TickerMetadata":
                q.filter.return_value = q
                q.order_by.return_value = q

                def all_side():
                    return meta_rows

                q.all.side_effect = all_side

                def first_side():
                    # per-ticker lookup in _classify_portfolio
                    return None

                q.first.side_effect = lambda: None
            return q

        db.query.side_effect = query_side

        # Fix first() per ticker for classification
        def meta_first():
            calls = []

            def _first():
                # simplified: return by filter chain — use all rows
                return None

            return _first

        with tempfile.TemporaryDirectory() as tmp:
            cat_path = os.path.join(tmp, "research_sectors.json")
            seed = {
                "version": "test",
                "source_doc": "test",
                "sectors": [{
                    "id": "information_technology",
                    "gics_name": "Information Technology",
                    "sector": "Technology",
                    "label": "Information Technology",
                    "subsectors": [],
                    "keywords": ["technology"],
                    "etf_spdr": "XLK",
                    "etf_alt": [],
                    "index": "test",
                    "seed_tickers": [],
                }],
            }
            with open(cat_path, "w") as f:
                json.dump(seed, f)

            with patch("ml_engine.sector_resolver._catalog_path", return_value=cat_path), \
                 patch("data_ingestion.sector_catalog_refresh._catalog_path", return_value=cat_path), \
                 patch("data_ingestion.sector_catalog_refresh._collect_refresh_universe", return_value=["NVDA", "AMD", "MSFT", "JPM"]), \
                 patch("data_ingestion.sector_catalog_refresh._metadata_rows", return_value=meta_rows), \
                 patch("data_ingestion.sector_catalog_refresh._classify_portfolio") as mock_cls:
                mock_cls.return_value = (
                    [{"ticker": "NVDA", "sector": "Technology", "sources": ["virtual"]}],
                    {"Technology": [{"ticker": "NVDA", "sources": ["virtual"]}]},
                )
                result = refresh_catalog(db=db, top_n=3, fetch=False)

            self.assertEqual(result["status"], "ok")
            with open(cat_path) as f:
                out = json.load(f)
            tech_seeds = out["sectors"][0]["seed_tickers"]
            self.assertEqual(tech_seeds[0]["ticker"], "NVDA")
            self.assertEqual(tech_seeds[1]["ticker"], "MSFT")
            self.assertEqual(tech_seeds[2]["ticker"], "AMD")


if __name__ == "__main__":
    unittest.main()
