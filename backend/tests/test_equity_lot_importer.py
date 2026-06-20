"""Tests for equity lot PDF import parsers."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_ingestion.equity_lot_importer import (
    parse_schwab_lot_details,
    parse_etrade_stock_plan,
    dedupe_against_existing,
    lot_fingerprint,
)


SCHWAB_SNIPPET = """
Lot Details: PINS - PINTEREST INC CLASS A
Cost Basis CalculatorHelpExportPrint
611 $20.54 $18.68 $12,549.94 $11,413.48 +$1,136.46 +9.96% Short Term
466 $20.54 $18.68 $9,571.64 $8,704.88 +$866.76 +9.96% Short Term
Quantity Price Cost/Share Market Value Cost Basis Gain/Loss $ Gain/Loss % Holding PeriodOpen Date
03/20/2026
03/20/2026
"""

ETRADE_SNIPPET = """
Stock Plan (ADBE) -7838
Purchase Date Purchase Price Purchased Qty.Sellable Qty.Est. Current Mark
12/29/2017 $81.02 61 6 $1,170.96
06/29/2018 $81.02 215 215 $41,959.40
Grant Date Granted Qty.Vested Qty.Sellable Qty.Est. Current Market Value
06/15/2016700 700 332 $64,793.12
"""


def test_schwab_parser():
    r = parse_schwab_lot_details(SCHWAB_SNIPPET)
    assert r["ticker"] == "PINS"
    assert len(r["lots"]) == 2
    assert r["lots"][0]["shares"] == 611
    assert r["lots"][0]["cost_basis_per_share"] == 18.68
    assert r["lots"][0]["acquisition_date"] == "2026-03-20"


def test_etrade_espp_parser():
    r = parse_etrade_stock_plan(ETRADE_SNIPPET)
    assert r["ticker"] == "ADBE"
    assert len(r["lots"]) == 2
    assert r["lots"][0]["lot_type"] == "espp"
    assert r["lots"][0]["shares"] == 6
    assert any("restricted-stock" in w.lower() for w in r["warnings"])


def test_dedupe():
    lots = [{"ticker": "PINS", "account_label": "Charles Schwab", "lot_type": "rsu",
             "shares": 100, "cost_basis_per_share": 10.0, "acquisition_date": "2024-01-01"}]

    class Row:
        ticker = "PINS"
        account_label = "Charles Schwab"
        lot_type = "rsu"
        shares = 100
        cost_basis_per_share = 10.0
        acquisition_date = "2024-01-01"

    new, skipped = dedupe_against_existing(lots, [Row()])
    assert len(new) == 0
    assert len(skipped) == 1
    assert lot_fingerprint(lots[0]) == (
        "PINS", "charles schwab", "rsu", 100.0, 10.0, "2024-01-01"
    )
