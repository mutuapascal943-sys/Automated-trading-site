"""Symbol name mappings between model prefixes and Deriv API symbols.

Model files are prefixed with a short canonical code (``EURUSD``, ``CRASH1000``,
``R_75``, ...) so inference can match frontend labels like "EUR/USD" to a trained
model. Deriv's ``ticks_history`` / ``ticks`` API expects its own symbol
names (``frxEURUSD``, ``cryBTCUSD``, ``R_75``, ...) loaded from symbol_map.json.
"""

from __future__ import annotations
from trading_app.trading_bot.symbol_map import load_symbol_map


def deriv_symbol_for(symbol: str) -> str:
    """Map a short training prefix to its Deriv API symbol via symbol_map.json.

    Unknown symbols (already-valid Deriv names, adapter-specific codes) are
    returned unchanged.
    """
    try:
        sm = load_symbol_map()
        if symbol in sm.canonical_symbols:
            return sm.to_broker(symbol, "deriv")
    except Exception:
        pass
    return symbol
