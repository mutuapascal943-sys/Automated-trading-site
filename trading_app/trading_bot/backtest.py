from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Callable, Protocol

from .interface import BrokerAdapter, Candle, OrderRequest
from .paper_broker import PaperBrokerAdapter
from .risk_engine import RiskEngine


class StrategyFn(Protocol):
    def __call__(self, candle: Candle, positions: list, balance: Decimal) -> OrderRequest | None:
        ...


@dataclass
class BacktestResult:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: Decimal = Decimal("0")
    max_drawdown: Decimal = Decimal("0")
    peak_equity: Decimal = Decimal("0")
    final_balance: Decimal = Decimal("0")
    trades: list = field(default_factory=list)
    equity_curve: list[Decimal] = field(default_factory=list)


def run_backtest(
    broker: PaperBrokerAdapter,
    candles: list[Candle],
    strategy: StrategyFn,
    risk_engine: RiskEngine | None = None,
    initial_balance: Decimal = Decimal("10000"),
) -> BacktestResult:
    if not candles:
        return BacktestResult(final_balance=initial_balance)

    broker.connect({})
    broker._balance = initial_balance

    result = BacktestResult()
    peak_equity = initial_balance

    candles_sorted = sorted(candles, key=lambda c: c.timestamp)

    for i, candle in enumerate(candles_sorted):
        broker._candle_store = {candle.symbol: candles_sorted[: i + 1]}

        broker.update_position_prices(candle.symbol, candle.close)

        balance_info = broker.get_balance()
        current_equity = balance_info.equity

        if current_equity > peak_equity:
            peak_equity = current_equity
        drawdown = (peak_equity - current_equity) / peak_equity if peak_equity > 0 else Decimal("0")
        if drawdown > result.max_drawdown:
            result.max_drawdown = drawdown

        result.equity_curve.append(current_equity)

        positions = broker.get_open_positions()

        signal = strategy(candle, positions, current_equity)

        if signal is not None:
            if signal.price is None:
                signal = OrderRequest(
                    symbol=signal.symbol,
                    side=signal.side,
                    order_type=signal.order_type,
                    volume=signal.volume,
                    price=candle.close,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                )
            if risk_engine:
                signal = risk_engine.apply_rules(candle.symbol, signal, current_equity)

            if signal is not None:
                try:
                    broker.place_order(signal)
                except Exception:
                    pass

    for pos in list(broker.get_open_positions()):
        trade = broker.close_position(pos.id)
        if trade:
            result.trades.append(trade)
            result.total_pnl += trade.pnl
            if trade.pnl > 0:
                result.winning_trades += 1
            else:
                result.losing_trades += 1

    result.total_trades = len(result.trades)
    result.final_balance = broker.get_balance().total
    result.peak_equity = peak_equity

    return result
