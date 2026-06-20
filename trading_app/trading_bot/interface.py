from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Optional


@dataclass(frozen=True)
class Candle:
    symbol: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    timestamp: datetime  # UTC
    granularity: int  # seconds, e.g. 60, 300, 900, 3600, 86400

    def mid_price(self) -> Decimal:
        return (self.high + self.low) / Decimal("2")


@dataclass(frozen=True)
class Tick:
    symbol: str
    bid: Decimal
    ask: Decimal
    timestamp: datetime  # UTC
    volume: Optional[Decimal] = None


@dataclass(frozen=True)
class Balance:
    total: Decimal
    currency: str
    available: Decimal
    equity: Decimal  # total + unrealized PnL


@dataclass(frozen=True)
class Order:
    id: str
    symbol: str
    side: str  # buy / sell
    order_type: str  # market / limit / stop
    volume: Decimal
    price: Optional[Decimal]  # None for market orders until filled
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    status: str = "pending"  # pending / open / filled / cancelled / rejected
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    filled_at: Optional[datetime] = None
    filled_price: Optional[Decimal] = None
    broker_order_id: Optional[str] = None


@dataclass(frozen=True)
class Position:
    id: str
    symbol: str
    side: str  # buy / sell
    volume: Decimal
    entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal
    opened_at: datetime
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    broker_position_id: Optional[str] = None


@dataclass(frozen=True)
class Trade:
    id: str
    symbol: str
    side: str
    volume: Decimal
    entry_price: Decimal
    exit_price: Decimal
    pnl: Decimal
    entry_time: datetime
    exit_time: datetime
    broker_trade_id: Optional[str] = None


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str
    order_type: str
    volume: Decimal
    price: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None


TickCallback = Callable[[Tick], None]


class BrokerAdapter(abc.ABC):

    @abc.abstractmethod
    def connect(self, credentials: dict[str, str]) -> None:
        ...

    @abc.abstractmethod
    def disconnect(self) -> None:
        ...

    @abc.abstractmethod
    def get_candles(self, symbol: str, granularity: int, count: int) -> list[Candle]:
        ...

    @abc.abstractmethod
    def subscribe_ticks(self, symbol: str, on_tick: TickCallback) -> None:
        ...

    @abc.abstractmethod
    def unsubscribe_ticks(self, symbol: str) -> None:
        ...

    @abc.abstractmethod
    def get_balance(self) -> Balance:
        ...

    @abc.abstractmethod
    def place_order(self, request: OrderRequest) -> Order:
        ...

    @abc.abstractmethod
    def cancel_order(self, order_id: str) -> None:
        ...

    @abc.abstractmethod
    def get_open_positions(self) -> list[Position]:
        ...

    @abc.abstractmethod
    def get_trade_history(self, date_range: tuple[datetime, datetime]) -> list[Trade]:
        ...
