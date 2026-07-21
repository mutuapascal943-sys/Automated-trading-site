from __future__ import annotations

from decouple import config

from trading_app.trading_bot.interface import BrokerAdapter
from trading_app.trading_bot.paper_broker import PaperBrokerAdapter
from trading_app.trading_bot.deriv_adapter import DerivAdapter
from trading_app.trading_bot.binance_adapter import BinanceAdapter

ADAPTER_MAP: dict[str, type[BrokerAdapter]] = {
    "Deriv": DerivAdapter,
    "Binance": BinanceAdapter,
}


def get_adapter_for_broker(broker_name: str) -> BrokerAdapter:
    cls = ADAPTER_MAP.get(broker_name)
    if cls is not None:
        if cls is DerivAdapter:
            return cls(app_id=config("DERIV_APP_ID", default="1089"))
        return cls()

    api_key = config("TRADING_API_KEY", default="")
    if api_key and api_key != "sk-your-openai-api-key" and not api_key.startswith("sk-your"):
        return DerivAdapter(app_id=config("DERIV_APP_ID", default="1089"))

    return PaperBrokerAdapter()


def build_credentials(user) -> dict[str, str]:
    from .credential_encrypt import decrypt

    creds: dict[str, str] = {}
    key_raw = user.broker_api_key or ""
    secret_raw = user.broker_api_secret or ""

    creds["token"] = decrypt(key_raw) if key_raw else config("TRADING_API_KEY", default="")
    creds["api_key"] = decrypt(key_raw) if key_raw else config("TRADING_API_KEY", default="")
    creds["api_secret"] = decrypt(secret_raw) if secret_raw else config("TRADING_API_SECRET", default="")

    account_id = user.broker_account_id or config("TRADING_ACCOUNT_ID", default="")
    if account_id:
        creds["account_id"] = account_id

    return creds
