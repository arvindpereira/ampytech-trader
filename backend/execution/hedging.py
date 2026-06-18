"""Shared long-short hedging logic used by the suggestions API, the backtest, and the executor,
so all three agree on which instrument to short and how much.

Two modes:
  * beta_neutral: short the index (SPY or QQQ) the stock is most correlated with, sized by an
    estimated beta (correlation x relative volatility, clamped).
  * pair_trade: short a sector peer 1:1.
"""

# Sector-peer map for pair-trade hedging.
PEER_MAP = {
    "MSFT": "AAPL", "AAPL": "MSFT",
    "GOOGL": "META", "META": "GOOGL",
    "INTC": "AMD", "AMD": "INTC",
    "NVDA": "AMD", "ARM": "AMD",
    "QCOM": "AVGO", "AVGO": "QCOM",
    "CSCO": "NOK", "NOK": "CSCO",
    "ORCL": "IBM", "IBM": "ORCL",
    "TSM": "ASML", "ASML": "TSM",
    "AMZN": "META", "MU": "INTC",
    "PLTR": "ORCL", "SMCI": "NVDA",
    "BB": "NOK",
}

BETA_MIN, BETA_MAX = 0.3, 2.5
VALID_MODES = ("none", "beta_neutral", "pair_trade")


def compute_hedge(symbol, mode, corr_spy=0.8, corr_qqq=0.8,
                  rel_vol_spy=1.0, rel_vol_qqq=1.0, universe=None):
    """Returns (hedge_symbol, hedge_ratio) for a long in `symbol`, or (None, 0.0) if no hedge.

    `hedge_ratio` is the $ of the short per $1 of the long (beta for beta_neutral, 1.0 for pairs).
    """
    universe = universe or []
    if mode == "beta_neutral":
        if corr_qqq > corr_spy:
            hedge_symbol, beta = "QQQ", corr_qqq * rel_vol_qqq
        else:
            hedge_symbol, beta = "SPY", corr_spy * rel_vol_spy
        beta = max(BETA_MIN, min(BETA_MAX, beta))
        return hedge_symbol, beta
    elif mode == "pair_trade":
        peer = PEER_MAP.get(symbol)
        if peer and (not universe or peer in universe):
            return peer, 1.0
        return "SPY", 1.0
    return None, 0.0
