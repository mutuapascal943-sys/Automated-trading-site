from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable, Optional

from .interface import OrderRequest
from .logging_utils import AuditLogger, NullAuditLogger


@dataclass
class PositionSizing:
    method: str = "fixed"  # fixed | percent_balance | kelly | volatility_adjusted
    fixed_volume: Decimal = Decimal("0.01")
    risk_percent: Decimal = Decimal("1")  # percent of balance to risk per trade
    max_risk_percent: Decimal = Decimal("2")
    max_position_size: Decimal = Decimal("100")
    win_rate: Decimal = Decimal("0.5")  # for Kelly: historical win rate 0-1
    avg_win: Decimal = Decimal("2.0")  # for Kelly: avg win / avg loss ratio
    avg_loss: Decimal = Decimal("1.0")


@dataclass
class RiskRule:
    name: str
    enabled: bool = True
    params: dict = field(default_factory=dict)
    check: Optional[Callable[..., bool]] = None


class RiskEngine:
    def __init__(
        self,
        sizing: PositionSizing | None = None,
        max_exposure_percent: Decimal = Decimal("20"),
        max_daily_loss_percent: Decimal = Decimal("10"),
        default_stop_loss_percent: Decimal = Decimal("1"),
        default_take_profit_percent: Decimal = Decimal("2"),
        trailing_stop_percent: Decimal | None = None,
        account_balance: Decimal = Decimal("0"),
        logger: AuditLogger | None = None,
    ) -> None:
        self._sizing = sizing or PositionSizing()
        self._max_exposure_pct = max_exposure_percent
        self._max_daily_loss_pct = max_daily_loss_percent
        self._default_sl_pct = default_stop_loss_percent
        self._default_tp_pct = default_take_profit_percent
        self._trailing_stop_pct = trailing_stop_percent
        self._account_balance = account_balance
        self._logger = logger or NullAuditLogger()
        self._custom_rules: list[RiskRule] = []
        self._daily_pnl = Decimal("0")
        self._current_exposure = Decimal("0")

    def reset_daily(self) -> None:
        self._daily_pnl = Decimal("0")
        self._current_exposure = Decimal("0")

    def apply_rules(
        self,
        symbol: str,
        signal: OrderRequest,
        balance: Decimal,
        open_positions: list | None = None,
    ) -> OrderRequest | None:
        open_positions = open_positions or []

        volume = self._compute_volume(signal.side, balance, signal.volume)

        if volume <= Decimal("0"):
            self._logger.log_risk("position_sizing", triggered=True, details={
                "symbol": symbol, "reason": "zero_volume",
            })
            return None

        signal = OrderRequest(
            symbol=signal.symbol,
            side=signal.side,
            order_type=signal.order_type,
            volume=volume,
            price=signal.price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
        )

        total_exposure = self._calculate_total_exposure(open_positions, volume)
        if not self._check_exposure(total_exposure, balance):
            self._logger.log_risk("max_exposure", triggered=True, details={
                "symbol": symbol, "volume": str(volume), "balance": str(balance),
                "total_exposure": str(total_exposure),
            })
            return None

        if not self._check_daily_loss(balance):
            self._logger.log_risk("daily_loss_limit", triggered=True, details={
                "symbol": symbol, "daily_pnl": str(self._daily_pnl),
                "balance": str(balance),
            })
            return None

        max_open = self._check_max_open_positions(open_positions)
        if max_open is not None:
            self._logger.log_risk("max_open_positions", triggered=True, details={
                "symbol": symbol, "open_count": str(len(open_positions)),
            })
            return None

        signal = self._apply_default_stops(symbol, signal)

        for rule in self._custom_rules:
            if not rule.enabled:
                continue
            try:
                if rule.check and not rule.check(symbol=symbol, signal=signal, balance=balance):
                    self._logger.log_risk(rule.name, triggered=True, details={
                        "symbol": symbol, "reason": "custom_rule_blocked",
                    })
                    return None
            except Exception:
                self._logger.log_risk(rule.name, triggered=True, details={
                    "symbol": symbol, "reason": "rule_check_error",
                })
                return None

        self._logger.log_risk("all_rules", triggered=False, details={
            "symbol": symbol, "volume": str(volume),
        })
        return signal

    def add_rule(self, rule: RiskRule) -> None:
        self._custom_rules.append(rule)

    def record_trade_pnl(self, pnl: Decimal) -> None:
        self._daily_pnl += pnl

    def update_exposure(self, exposure: Decimal) -> None:
        self._current_exposure = exposure

    def update_trailing_stops(self, positions: list, current_prices: dict[str, Decimal]) -> list[tuple[str, Decimal]]:
        if self._trailing_stop_pct is None:
            return []

        updated = []
        for pos in positions:
            symbol = pos.symbol
            price = current_prices.get(symbol)
            if price is None:
                continue

            current_sl = pos.stop_loss
            if current_sl is None:
                continue

            if pos.side == "buy":
                new_sl = price * (Decimal("1") - self._trailing_stop_pct / Decimal("100"))
                if new_sl > current_sl:
                    updated.append((pos.id, new_sl))
            elif pos.side == "sell":
                new_sl = price * (Decimal("1") + self._trailing_stop_pct / Decimal("100"))
                if new_sl < current_sl:
                    updated.append((pos.id, new_sl))

        return updated

    def _compute_volume(self, side: str, balance: Decimal, requested_volume: Decimal) -> Decimal:
        if self._sizing.method == "fixed":
            return min(self._sizing.fixed_volume, self._sizing.max_position_size)

        elif self._sizing.method == "percent_balance":
            risk_amount = balance * (self._sizing.risk_percent / Decimal("100"))
            volume = (risk_amount / Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            return min(volume, self._sizing.max_position_size)

        elif self._sizing.method == "kelly":
            kelly_fraction = self._compute_kelly_fraction()
            if kelly_fraction <= Decimal("0"):
                return Decimal("0")
            volume = (balance * kelly_fraction / Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            return min(volume, self._sizing.max_position_size)

        elif self._sizing.method == "volatility_adjusted":
            return min(requested_volume, self._sizing.max_position_size)

        return requested_volume

    def _compute_kelly_fraction(self) -> Decimal:
        win_rate = self._sizing.win_rate
        avg_win = self._sizing.avg_win
        avg_loss = self._sizing.avg_loss

        if avg_loss == Decimal("0") or win_rate <= Decimal("0") or win_rate >= Decimal("1"):
            return Decimal("0")

        win_loss_ratio = avg_win / avg_loss
        kelly = (win_rate * win_loss_ratio - (Decimal("1") - win_rate)) / win_loss_ratio
        kelly = max(Decimal("0"), kelly)
        kelly = min(kelly, Decimal("0.25"))

        return (kelly * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def _calculate_total_exposure(
        self, open_positions: list, new_volume: Decimal
    ) -> Decimal:
        existing = sum(
            getattr(p, "volume", Decimal("0")) for p in open_positions
        )
        return existing + new_volume

    def _check_exposure(self, total_exposure: Decimal, balance: Decimal) -> bool:
        if balance <= Decimal("0"):
            return False
        max_exposure = balance * (self._max_exposure_pct / Decimal("100"))
        return total_exposure <= max_exposure

    def _check_daily_loss(self, balance: Decimal) -> bool:
        if balance <= Decimal("0"):
            return False
        max_loss_amount = balance * (self._max_daily_loss_pct / Decimal("100"))
        return self._daily_pnl >= -max_loss_amount

    def _check_max_open_positions(self, open_positions: list) -> str | None:
        if len(open_positions) >= 10:
            return "max_positions_reached"
        return None

    def _apply_default_stops(self, symbol: str, signal: OrderRequest) -> OrderRequest:
        if signal.stop_loss is None and signal.price:
            sl = signal.price * (Decimal("1") - self._default_sl_pct / Decimal("100"))
            signal = OrderRequest(
                symbol=signal.symbol,
                side=signal.side,
                order_type=signal.order_type,
                volume=signal.volume,
                price=signal.price,
                stop_loss=sl,
                take_profit=signal.take_profit,
            )

        if signal.take_profit is None and signal.price:
            tp = signal.price * (Decimal("1") + self._default_tp_pct / Decimal("100"))
            signal = OrderRequest(
                symbol=signal.symbol,
                side=signal.side,
                order_type=signal.order_type,
                volume=signal.volume,
                price=signal.price,
                stop_loss=signal.stop_loss,
                take_profit=tp,
            )

        return signal
