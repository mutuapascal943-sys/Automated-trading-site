from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

from .interface import (
    Balance,
    BrokerAdapter,
    Candle,
    Order,
    OrderRequest,
    Position,
    Tick,
    TickCallback,
    Trade,
)
from .errors import InsufficientFunds, InvalidSymbol, OrderRejected


class PaperBrokerAdapter(BrokerAdapter):
    def __init__(
        self,
        initial_balance: Decimal = Decimal("10000"),
        currency: str = "USD",
        slippage_bps: int = 5,
        latencies_ms: tuple[int, int] = (50, 200),
    ) -> None:
        self._initial_balance = initial_balance
        self._currency = currency
        self._slippage_bps = slippage_bps
        self._latencies_ms = latencies_ms

        self._connected = False
        self._balance = initial_balance
        self._equity = initial_balance
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, Order] = {}
        self._trades: list[Trade] = []
        self._tick_subscribers: dict[str, list[TickCallback]] = {}
        self._candle_store: dict[str, list[Candle]] = {}

    def connect(self, credentials: dict[str, str]) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False
        self._tick_subscribers.clear()

    def get_candles(self, symbol: str, granularity: int, count: int) -> list[Candle]:
        candles = self._candle_store.get(symbol, [])
        return candles[-count:]

    def seed_candles(self, candles: list[Candle]) -> None:
        for c in candles:
            self._candle_store.setdefault(c.symbol, []).append(c)

    def subscribe_ticks(self, symbol: str, on_tick: TickCallback) -> None:
        self._tick_subscribers.setdefault(symbol, []).append(on_tick)

    def unsubscribe_ticks(self, symbol: str) -> None:
        self._tick_subscribers.pop(symbol, None)

    def inject_tick(self, tick: Tick) -> None:
        callbacks = self._tick_subscribers.get(tick.symbol, [])
        for cb in callbacks:
            cb(tick)

    def get_balance(self) -> Balance:
        unrealized = Decimal("0")
        for pos in self._positions.values():
            unrealized += pos.unrealized_pnl
        return Balance(
            total=self._balance + unrealized,
            currency=self._currency,
            available=self._balance,
            equity=self._balance + unrealized,
        )

    def place_order(self, request: OrderRequest) -> Order:
        if not self._connected:
            raise OrderRejected("Paper broker not connected")

        if request.volume <= Decimal("0"):
            raise OrderRejected("Volume must be positive")

        cost = self._estimate_cost(request)
        if cost > self._balance:
            raise InsufficientFunds(f"Need {cost} but have {self._balance}")

        order_id = str(uuid.uuid4())
        candle = self._last_candle(request.symbol)
        base_price = request.price or (candle.close if candle else Decimal("1"))
        fill_price = self._apply_slippage(base_price, request.side)

        order = Order(
            id=order_id,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            volume=request.volume,
            price=fill_price,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            status="filled",
            created_at=datetime.now(timezone.utc),
            filled_at=datetime.now(timezone.utc),
            filled_price=fill_price,
        )
        self._orders[order_id] = order

        pos_id = str(uuid.uuid4())
        position = Position(
            id=pos_id,
            symbol=request.symbol,
            side=request.side,
            volume=request.volume,
            entry_price=fill_price,
            current_price=fill_price,
            unrealized_pnl=Decimal("0"),
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            opened_at=datetime.now(timezone.utc),
        )
        self._positions[pos_id] = position
        self._balance -= cost

        return order

    def _apply_slippage(self, price: Decimal, side: str) -> Decimal:
        slippage_factor = Decimal(str(self._slippage_bps)) / Decimal("10000")
        if side == "buy":
            return price * (Decimal("1") + slippage_factor)
        else:
            return price * (Decimal("1") - slippage_factor)

    def cancel_order(self, order_id: str) -> None:
        order = self._orders.get(order_id)
        if order and order.status == "pending":
            self._orders[order_id] = Order(
                id=order.id,
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                volume=order.volume,
                price=order.price,
                stop_loss=order.stop_loss,
                take_profit=order.take_profit,
                status="cancelled",
                created_at=order.created_at,
            )

    def get_open_positions(self) -> list[Position]:
        return list(self._positions.values())

    def get_trade_history(self, date_range: tuple[datetime, datetime]) -> list[Trade]:
        start, end = date_range
        return [t for t in self._trades if start <= t.exit_time <= end]

    def close_position(self, position_id: str, price: Decimal | None = None) -> Trade | None:
        pos = self._positions.get(position_id)
        if pos is None:
            return None

        exit_price = price or pos.current_price
        pnl = self._calculate_pnl(pos, exit_price)

        trade = Trade(
            id=str(uuid.uuid4()),
            symbol=pos.symbol,
            side=pos.side,
            volume=pos.volume,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            pnl=pnl,
            entry_time=pos.opened_at,
            exit_time=datetime.now(timezone.utc),
        )
        self._trades.append(trade)
        self._balance += pos.entry_price * pos.volume + pnl
        del self._positions[position_id]
        return trade

    def update_position_prices(self, symbol: str, current_price: Decimal) -> None:
        for pos_id, pos in list(self._positions.items()):
            if pos.symbol != symbol:
                continue
            pnl = self._calculate_pnl(pos, current_price)
            self._positions[pos_id] = Position(
                id=pos.id,
                symbol=pos.symbol,
                side=pos.side,
                volume=pos.volume,
                entry_price=pos.entry_price,
                current_price=current_price,
                unrealized_pnl=pnl,
                stop_loss=pos.stop_loss,
                take_profit=pos.take_profit,
                opened_at=pos.opened_at,
            )

            if pos.stop_loss and (
                (pos.side == "buy" and current_price <= pos.stop_loss)
                or (pos.side == "sell" and current_price >= pos.stop_loss)
            ):
                self.close_position(pos_id, pos.stop_loss)
            elif pos.take_profit and (
                (pos.side == "buy" and current_price >= pos.take_profit)
                or (pos.side == "sell" and current_price <= pos.take_profit)
            ):
                self.close_position(pos_id, pos.take_profit)

    def _last_candle(self, symbol: str) -> Candle | None:
        candles = self._candle_store.get(symbol, [])
        return candles[-1] if candles else None

    def _estimate_cost(self, request: OrderRequest) -> Decimal:
        ref_price = request.price or Decimal("1")
        return ref_price * request.volume

    def _calculate_pnl(self, pos: Position, exit_price: Decimal) -> Decimal:
        if pos.side == "buy":
            return (exit_price - pos.entry_price) * pos.volume
        else:
            return (pos.entry_price - exit_price) * pos.volume
