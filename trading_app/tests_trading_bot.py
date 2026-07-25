from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.test import TestCase

from trading_app.trading_bot.interface import (
    Balance,
    BrokerAdapter,
    Candle,
    Order,
    OrderRequest,
    Position,
    Tick,
    Trade,
)
from trading_app.trading_bot.errors import (
    BrokerError,
    ConnectionLost,
    InsufficientFunds,
    InvalidSymbol,
    OrderRejected,
    RateLimited,
)
from trading_app.trading_bot.paper_broker import PaperBrokerAdapter
from trading_app.trading_bot.symbol_map import SymbolMap, load_symbol_map
from trading_app.trading_bot.risk_engine import RiskEngine, PositionSizing, RiskRule
from trading_app.trading_bot.logging_utils import ConsoleAuditLogger, NullAuditLogger
from trading_app.trading_bot.backtest import BacktestResult, run_backtest


def make_candle(
    symbol: str = "EURUSD",
    o: str = "1.1000",
    h: str = "1.1050",
    l: str = "1.0950",
    c: str = "1.1020",
    v: str = "1000",
    ts: datetime | None = None,
    granularity: int = 3600,
) -> Candle:
    return Candle(
        symbol=symbol,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        volume=Decimal(v),
        timestamp=ts or datetime(2024, 1, 1, tzinfo=timezone.utc),
        granularity=granularity,
    )


class InterfaceTests(TestCase):
    def test_candle_creation(self) -> None:
        c = make_candle()
        self.assertEqual(c.symbol, "EURUSD")
        self.assertEqual(c.open, Decimal("1.1000"))
        self.assertEqual(c.mid_price(), Decimal("1.1000"))

    def test_tick_creation(self) -> None:
        t = Tick(symbol="EURUSD", bid=Decimal("1.1000"), ask=Decimal("1.1002"), timestamp=datetime.now(timezone.utc))
        self.assertEqual(t.bid, Decimal("1.1000"))
        self.assertEqual(t.ask, Decimal("1.1002"))

    def test_balance_creation(self) -> None:
        b = Balance(total=Decimal("10000"), currency="USD", available=Decimal("9000"), equity=Decimal("10500"))
        self.assertEqual(b.currency, "USD")
        self.assertEqual(b.equity, Decimal("10500"))

    def test_order_request(self) -> None:
        r = OrderRequest(symbol="EURUSD", side="buy", order_type="market", volume=Decimal("0.01"))
        self.assertEqual(r.side, "buy")
        self.assertEqual(r.order_type, "market")

    def test_broker_adapter_is_abstract(self) -> None:
        with self.assertRaises(TypeError):
            BrokerAdapter()  # type: ignore[abstract]


class PaperBrokerTests(TestCase):
    def setUp(self) -> None:
        self.broker = PaperBrokerAdapter(initial_balance=Decimal("10000"))

    def test_connect_disconnect(self) -> None:
        self.broker.connect({})
        bal = self.broker.get_balance()
        self.assertEqual(bal.total, Decimal("10000"))
        self.broker.disconnect()

    def test_place_and_fill_market_order(self) -> None:
        self.broker.connect({})
        self.broker.seed_candles([make_candle()])

        order = self.broker.place_order(OrderRequest(
            symbol="EURUSD", side="buy", order_type="market", volume=Decimal("0.01"),
        ))
        self.assertEqual(order.status, "filled")
        self.assertIsNotNone(order.filled_price)

    def test_insufficient_funds(self) -> None:
        self.broker.connect({})
        self.broker.seed_candles([make_candle()])

        with self.assertRaises(InsufficientFunds):
            self.broker.place_order(OrderRequest(
                symbol="EURUSD", side="buy", order_type="market", volume=Decimal("100000"),
            ))

    def test_inject_tick(self) -> None:
        received: list[Tick] = []
        self.broker.connect({})

        def on_tick(t: Tick) -> None:
            received.append(t)

        self.broker.subscribe_ticks("EURUSD", on_tick)
        tick = Tick(symbol="EURUSD", bid=Decimal("1.1000"), ask=Decimal("1.1002"), timestamp=datetime.now(timezone.utc))
        self.broker.inject_tick(tick)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].bid, Decimal("1.1000"))

    def test_open_and_close_position(self) -> None:
        self.broker.connect({})
        self.broker.seed_candles([make_candle()])

        self.broker.place_order(OrderRequest(
            symbol="EURUSD", side="buy", order_type="market", volume=Decimal("1"),
        ))
        positions = self.broker.get_open_positions()
        self.assertEqual(len(positions), 1)

        trade = self.broker.close_position(positions[0].id, price=Decimal("1.2000"))
        self.assertIsNotNone(trade)
        self.assertGreater(trade.pnl, Decimal("0"))  # type: ignore[operator]

    def test_stop_loss_trigger(self) -> None:
        self.broker.connect({})
        self.broker.seed_candles([make_candle()])

        self.broker.place_order(OrderRequest(
            symbol="EURUSD", side="buy", order_type="market", volume=Decimal("1"),
            stop_loss=Decimal("1.0000"),
        ))

        self.broker.update_position_prices("EURUSD", Decimal("0.9999"))
        positions = self.broker.get_open_positions()
        self.assertEqual(len(positions), 0)

    def test_take_profit_trigger(self) -> None:
        self.broker.connect({})
        self.broker.seed_candles([make_candle()])

        self.broker.place_order(OrderRequest(
            symbol="EURUSD", side="buy", order_type="market", volume=Decimal("1"),
            take_profit=Decimal("1.2000"),
        ))

        self.broker.update_position_prices("EURUSD", Decimal("1.2001"))
        positions = self.broker.get_open_positions()
        self.assertEqual(len(positions), 0)

    def test_trade_history(self) -> None:
        self.broker.connect({})
        self.broker.seed_candles([make_candle()])
        self.broker.place_order(OrderRequest(
            symbol="EURUSD", side="buy", order_type="market", volume=Decimal("1"),
        ))
        positions = self.broker.get_open_positions()
        self.broker.close_position(positions[0].id, price=Decimal("1.1500"))

        now = datetime.now(timezone.utc)
        history = self.broker.get_trade_history((
            datetime(2020, 1, 1, tzinfo=timezone.utc),
            now + timedelta(days=365),
        ))
        self.assertEqual(len(history), 1)

    def test_get_balance_after_trades(self) -> None:
        self.broker.connect({})
        self.broker.seed_candles([make_candle()])

        bal_before = self.broker.get_balance()
        self.assertEqual(bal_before.total, Decimal("10000"))

        self.broker.place_order(OrderRequest(
            symbol="EURUSD", side="buy", order_type="market", volume=Decimal("1"),
        ))
        bal_after = self.broker.get_balance()
        self.assertLess(bal_after.total, Decimal("10000"))

    def test_cancel_order(self) -> None:
        self.broker.connect({})
        order = Order(
            id="test-id",
            symbol="EURUSD",
            side="buy",
            order_type="limit",
            volume=Decimal("1"),
            price=Decimal("1.0000"),
            status="pending",
        )
        self.broker._orders["test-id"] = order
        self.broker.cancel_order("test-id")
        cancelled = self.broker._orders["test-id"]
        self.assertEqual(cancelled.status, "cancelled")


class SymbolMapTests(TestCase):
    def test_load_from_file(self) -> None:
        sm = load_symbol_map()
        self.assertIsInstance(sm, SymbolMap)

    def test_to_broker(self) -> None:
        sm = load_symbol_map()
        native = sm.to_broker("EURUSD", "deriv")
        self.assertEqual(native, "EUR/USD")

    def test_to_canonical(self) -> None:
        sm = load_symbol_map()
        canonical = sm.to_canonical("EUR/USD", "deriv")
        self.assertEqual(canonical, "EURUSD")

    def test_invalid_symbol_raises(self) -> None:
        sm = load_symbol_map()
        with self.assertRaises(KeyError):
            sm.to_broker("NONEXISTENT", "deriv")

    def test_canonical_symbols(self) -> None:
        sm = load_symbol_map()
        self.assertIn("EURUSD", sm.canonical_symbols)

    def test_json_is_valid(self) -> None:
        path = Path(__file__).parent / "trading_bot" / "symbol_map.json"
        with open(path) as f:
            data = json.load(f)
        self.assertIsInstance(data, dict)
        self.assertIn("EURUSD", data)


class ErrorTests(TestCase):
    def test_insufficient_funds(self) -> None:
        e = InsufficientFunds("Not enough money", original_code="InsufficientBalance")
        self.assertIsInstance(e, BrokerError)
        self.assertEqual(e.original_code, "InsufficientBalance")
        self.assertIn("money", str(e))

    def test_invalid_symbol(self) -> None:
        e = InvalidSymbol("Bad symbol")
        self.assertIsInstance(e, BrokerError)

    def test_rate_limited(self) -> None:
        e = RateLimited("Too fast")
        self.assertIsInstance(e, BrokerError)

    def test_connection_lost(self) -> None:
        e = ConnectionLost("Disconnected")
        self.assertIsInstance(e, BrokerError)

    def test_order_rejected(self) -> None:
        e = OrderRejected("Nope")
        self.assertIsInstance(e, BrokerError)


class RiskEngineTests(TestCase):
    def setUp(self) -> None:
        self.engine = RiskEngine(
            sizing=PositionSizing(method="fixed", fixed_volume=Decimal("0.1")),
            max_exposure_percent=Decimal("10"),
        )

    def test_passes_good_signal(self) -> None:
        signal = OrderRequest(symbol="EURUSD", side="buy", order_type="market", volume=Decimal("0.1"))
        result = self.engine.apply_rules("EURUSD", signal, Decimal("10000"))
        self.assertIsNotNone(result)

    def test_blocks_excessive_volume(self) -> None:
        self.engine._sizing.max_position_size = Decimal("0.05")
        signal = OrderRequest(symbol="EURUSD", side="buy", order_type="market", volume=Decimal("100"))
        result = self.engine.apply_rules("EURUSD", signal, Decimal("10000"))
        self.assertIsNotNone(result)
        self.assertLessEqual(result.volume, Decimal("0.05"))

    def test_blocks_when_over_exposure(self) -> None:
        self.engine.update_exposure(Decimal("1000"))
        signal = OrderRequest(symbol="EURUSD", side="buy", order_type="market", volume=Decimal("10000"))
        result = self.engine.apply_rules("EURUSD", signal, Decimal("10000"))
        self.assertIsNone(result)

    def test_applies_default_stops(self) -> None:
        signal = OrderRequest(symbol="EURUSD", side="buy", order_type="market", volume=Decimal("0.1"), price=Decimal("1.1000"))
        result = self.engine.apply_rules("EURUSD", signal, Decimal("10000"))
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.stop_loss)
        self.assertIsNotNone(result.take_profit)

    def test_custom_rule_rejects(self) -> None:
        def reject_all(**kwargs: Any) -> bool:
            return False

        self.engine.add_rule(RiskRule(name="reject_all", check=reject_all))
        signal = OrderRequest(symbol="EURUSD", side="buy", order_type="market", volume=Decimal("0.1"))
        result = self.engine.apply_rules("EURUSD", signal, Decimal("10000"))
        self.assertIsNone(result)

    def test_daily_loss_limit(self) -> None:
        self.engine._max_daily_loss_pct = Decimal("5")
        self.engine.record_trade_pnl(Decimal("-10000"))
        signal = OrderRequest(symbol="EURUSD", side="buy", order_type="market", volume=Decimal("0.1"))
        result = self.engine.apply_rules("EURUSD", signal, Decimal("10000"))
        self.assertIsNone(result)

    def test_percent_sizing(self) -> None:
        engine = RiskEngine(
            sizing=PositionSizing(method="percent_balance", risk_percent=Decimal("2")),
        )
        signal = OrderRequest(symbol="EURUSD", side="buy", order_type="market", volume=Decimal("1"))
        result = engine.apply_rules("EURUSD", signal, Decimal("10000"))
        self.assertIsNotNone(result)
        self.assertEqual(result.volume, Decimal("2"))


class BacktestTests(TestCase):
    def test_empty_candles(self) -> None:
        broker = PaperBrokerAdapter(initial_balance=Decimal("10000"))
        result = run_backtest(broker, [], lambda c, p, b: None)
        self.assertEqual(result.total_trades, 0)
        self.assertEqual(result.final_balance, Decimal("10000"))

    def test_buy_and_hold_strategy(self) -> None:
        broker = PaperBrokerAdapter(initial_balance=Decimal("10000"))
        candles = [
            make_candle(c="1.1000", o="1.1000", ts=datetime(2024, 1, 1, tzinfo=timezone.utc)),
            make_candle(c="1.1100", o="1.1005", ts=datetime(2024, 1, 2, tzinfo=timezone.utc)),
            make_candle(c="1.1200", o="1.1105", ts=datetime(2024, 1, 3, tzinfo=timezone.utc)),
        ]

        def strategy(candle: Candle, positions: list, balance: Decimal) -> OrderRequest | None:
            if not positions:
                return OrderRequest(
                    symbol=candle.symbol, side="buy", order_type="market", volume=Decimal("1"),
                    price=candle.close,
                )
            return None

        result = run_backtest(broker, candles, strategy)
        self.assertGreater(result.total_trades, 0)
        self.assertGreater(result.total_pnl, Decimal("0"))

    def test_result_fields(self) -> None:
        result = BacktestResult(
            total_trades=5,
            winning_trades=3,
            losing_trades=2,
            total_pnl=Decimal("500"),
            max_drawdown=Decimal("0.05"),
            peak_equity=Decimal("10500"),
            final_balance=Decimal("10500"),
        )
        self.assertEqual(result.winning_trades, 3)
        self.assertEqual(result.total_pnl, Decimal("500"))


class AuditLoggerTests(TestCase):
    def test_null_logger_does_nothing(self) -> None:
        logger = NullAuditLogger()
        logger.log_order("placed", "123", {})
        logger.log_signal("EURUSD", "buy", 0.8, {})
        logger.log_connection("deriv", "connected", {})
        logger.log_error("test", "error", {})
        logger.log_risk("rule1", True, {})

    def test_console_logger_produces_json(self) -> None:
        logger = ConsoleAuditLogger()
        with self.assertLogs("trading_bot", level="INFO") as logs:
            logger.log_order("placed", "order-1", {"symbol": "EURUSD"})
            self.assertTrue(any("order-1" in log for log in logs.output))


class FullIntegrationTests(TestCase):
    def test_paper_broker_full_workflow(self) -> None:
        broker = PaperBrokerAdapter(initial_balance=Decimal("10000"))
        broker.connect({})

        candles = [
            make_candle(ts=datetime(2024, 1, 1, tzinfo=timezone.utc)),
            make_candle(c="1.1100", ts=datetime(2024, 1, 2, tzinfo=timezone.utc)),
        ]
        broker.seed_candles(candles)

        order = broker.place_order(OrderRequest(
            symbol="EURUSD", side="buy", order_type="market", volume=Decimal("1"),
        ))
        self.assertEqual(order.status, "filled")

        positions = broker.get_open_positions()
        self.assertEqual(len(positions), 1)

        broker.update_position_prices("EURUSD", Decimal("1.2000"))
        positions = broker.get_open_positions()
        trade = broker.close_position(positions[0].id, Decimal("1.2000"))
        self.assertIsNotNone(trade)
        self.assertGreater(trade.pnl, Decimal("0"))

        balance = broker.get_balance()
        self.assertGreater(balance.total, Decimal("10000"))

    def test_symbol_map_and_paper_broker(self) -> None:
        sm = load_symbol_map()
        deriv_symbol = sm.to_broker("EURUSD", "deriv")
        self.assertEqual(deriv_symbol, "EUR/USD")

        broker = PaperBrokerAdapter()
        broker.connect({})
        broker.seed_candles([make_candle()])
        order = broker.place_order(OrderRequest(
            symbol="EURUSD", side="buy", order_type="market", volume=Decimal("0.01"),
        ))
        self.assertEqual(order.status, "filled")

    def test_risk_engine_with_paper_broker(self) -> None:
        engine = RiskEngine(
            sizing=PositionSizing(method="fixed", fixed_volume=Decimal("0.1")),
            max_exposure_percent=Decimal("50"),
            default_stop_loss_percent=Decimal("2"),
            default_take_profit_percent=Decimal("4"),
        )

        signal = OrderRequest(
            symbol="EURUSD", side="buy", order_type="market",
            volume=Decimal("1"), price=Decimal("1.1000"),
        )
        result = engine.apply_rules("EURUSD", signal, Decimal("10000"))
        self.assertIsNotNone(result)
        self.assertEqual(result.volume, Decimal("0.1"))
        self.assertIsNotNone(result.stop_loss)
        self.assertIsNotNone(result.take_profit)

    def test_backtest_with_risk_engine(self) -> None:
        broker = PaperBrokerAdapter(initial_balance=Decimal("10000"))
        engine = RiskEngine(
            sizing=PositionSizing(method="fixed", fixed_volume=Decimal("0.1")),
            max_exposure_percent=Decimal("50"),
        )

        candles = [
            make_candle(c="1.1000", ts=datetime(2024, 1, 1, tzinfo=timezone.utc)),
            make_candle(c="1.1050", ts=datetime(2024, 1, 2, tzinfo=timezone.utc)),
            make_candle(c="1.1020", ts=datetime(2024, 1, 3, tzinfo=timezone.utc)),
            make_candle(c="1.1080", ts=datetime(2024, 1, 4, tzinfo=timezone.utc)),
            make_candle(c="1.1120", ts=datetime(2024, 1, 5, tzinfo=timezone.utc)),
        ]

        def strategy(candle: Candle, positions: list, balance: Decimal) -> OrderRequest | None:
            if not positions:
                signal = engine.apply_rules(
                    candle.symbol,
                    OrderRequest(
                        symbol=candle.symbol, side="buy",
                        order_type="market", volume=Decimal("1"),
                        price=candle.close,
                    ),
                    balance,
                )
                return signal
            return None

        result = run_backtest(broker, candles, strategy, risk_engine=engine)
        self.assertGreater(result.total_trades, 0)
        self.assertGreater(result.final_balance, Decimal("10000"))
