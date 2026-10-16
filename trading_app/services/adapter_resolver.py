from __future__ import annotations

from decouple import config

from trading_app.trading_bot.deriv_adapter import DerivAdapter
from trading_app.trading_bot.paper_broker import PaperBrokerAdapter


def get_adapter_for_broker(broker_name: str = "") -> DerivAdapter | PaperBrokerAdapter:
    """Return the supported read-only market-data adapter. Defaults to DerivAdapter unless MARKET_DATA_MODE=mock is explicitly set."""
    mode = config("MARKET_DATA_MODE", default="live").lower()
    if mode == "mock":
        return PaperBrokerAdapter()
    return DerivAdapter(app_id=config("DERIV_APP_ID", default=""))


def build_credentials(user) -> dict[str, str]:
    """Return no account credentials for public read-only Deriv data."""
    return {}
