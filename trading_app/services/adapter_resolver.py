from __future__ import annotations

from decouple import config

from trading_app.trading_bot.deriv_adapter import DerivAdapter


def get_adapter_for_broker(broker_name: str = "") -> DerivAdapter:
    """Return the supported read-only market-data adapter."""
    return DerivAdapter(app_id=config("DERIV_APP_ID", default="1089"))


def build_credentials(user) -> dict[str, str]:
    token = config("MARKET_DATA_API_TOKEN", default="")
    return {'token': token} if token else {}
