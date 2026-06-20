from __future__ import annotations

from decouple import config

from trading_app.trading_bot.interface import BrokerAdapter
from trading_app.trading_bot.paper_broker import PaperBrokerAdapter
from trading_app.trading_bot.deriv_adapter import DerivAdapter
from trading_app.trading_bot.binance_adapter import BinanceAdapter

ADAPTER_MAP: dict[str, type[BrokerAdapter]] = {
    "Deriv": DerivAdapter,
    "Binance": BinanceAdapter,
    "Exness": DerivAdapter,
    "XM": DerivAdapter,
    "FBS": DerivAdapter,
    "HFM (HotForex)": DerivAdapter,
    "IC Markets": DerivAdapter,
    "Pepperstone": DerivAdapter,
    "FXTM": DerivAdapter,
    "Tickmill": DerivAdapter,
    "RoboForex": DerivAdapter,
}


def get_adapter_for_broker(broker_name: str) -> BrokerAdapter:
    cls = ADAPTER_MAP.get(broker_name)
    if cls is None:
        return PaperBrokerAdapter()
    return cls(app_id=config("DERIV_APP_ID", default="1089"))


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
