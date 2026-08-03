"""Symbol name mappings between model prefixes and Deriv API symbols.

Model files are prefixed with a short canonical code (``EURUSD``, ``CRASH1000``,
``R_75``, ...) so inference can match frontend labels like "EUR/USD" to a trained
model. Deriv's ``ticks_history`` / ``ticks`` API, however, expects its own symbol
names (``frxEURUSD``, ``cryBTCUSD``, ``R_75``, ...). This module bridges the two.
"""

DERIV_SYMBOLS: dict[str, str] = {
    "EURUSD": "frxEURUSD",
    "GBPUSD": "frxGBPUSD",
    "USDJPY": "frxUSDJPY",
    "AUDUSD": "frxAUDUSD",
    "USDCAD": "frxUSDCAD",
    "NZDUSD": "frxNZDUSD",
    "XAUUSD": "frxXAUUSD",
    "BTCUSD": "cryBTCUSD",
    "BOOM1000": "BOOM1000",
    "CRASH1000": "CRASH1000",
    "R_75": "R_75",
    "R_100": "R_100",
}


def deriv_symbol_for(symbol: str) -> str:
    """Map a short training prefix to its Deriv API symbol.

    Unknown symbols (already-valid Deriv names, adapter-specific codes) are
    returned unchanged.
    """
    return DERIV_SYMBOLS.get(symbol, symbol)
