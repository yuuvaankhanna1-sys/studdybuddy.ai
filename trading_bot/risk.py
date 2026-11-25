"""RiskGuard: the bot can't break the rules because every order has to get past this check."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .config import RiskRules


class RuleViolation(Exception):
    pass


@dataclass
class TradePlan:
    symbol: str
    side: str
    qty: float
    entry: float
    stop: float
    take_profit: float
    risk_usd: float
    reward_usd: float
    notional: float
    quote_usd: float = 1.0   # USD value of 1 unit of the quote currency (CHF for AUDCHF)
    stop_widened_from: float | None = None  # set if the stop was widened to fit lot sizes

    @property
    def rr(self) -> float:
        return self.reward_usd / self.risk_usd


class RiskGuard:
    def __init__(self, rules: RiskRules):
        rules.validate()
        self.rules = rules

    def _round_qty(self, q: float, step: float | None) -> float:
        f = 10 ** self.rules.qty_decimals
        return math.floor(q * f) / f

    def size(self, symbol: str, side: str, entry: float, stop: float,
             quote_usd: float = 1.0, qty_step: float | None = None,
             min_qty: float | None = None, max_stop_dist: float | None = None) -> TradePlan:
        """quote_usd: $ value of a 1.0 price move per unit (CHF->USD for AUDCHF).
        qty_step:  broker size step in units (1000 = 0.01 forex lot).
        With lot steps, picks the lot count whose risk lands in $5-6. If none fits the
        stop exactly, the stop is WIDENED (never tightened) up to max_stop_dist."""
        r = self.rules
        if quote_usd <= 0:
            raise RuleViolation("invalid currency conversion rate")
        stop_dist = abs(entry - stop)
        if stop_dist <= 0:
            raise RuleViolation("stop must differ from entry")
        if (side == "long" and stop >= entry) or (side == "short" and stop <= entry):
            raise RuleViolation("stop is on the wrong side of entry")

        original_stop = stop
        if qty_step:
            n0 = max(1, math.ceil((min_qty or qty_step) / qty_step - 1e-9))
            unit_risk = qty_step * stop_dist * quote_usd  # $ risk per lot step at this stop
            ideal = r.risk_target / unit_risk
            # 1) a lot count that keeps the stop where it is, risk closest to $5.50
            fits = [n for n in {math.floor(ideal), math.ceil(ideal)}
                    if n >= n0 and r.risk_min - 1e-6 <= n * unit_risk <= r.risk_max + 1e-6]
            choice = None
            if fits:
                n = min(fits, key=lambda k: abs(k * unit_risk - r.risk_target))
                choice = (n, stop_dist)
            else:
                # 2) no exact fit: use fewer lots and WIDEN the stop so risk = $5.50
                n = math.ceil(r.risk_min / unit_risk - 1e-9) - 1   # most lots that still risk < $5
                if n >= n0:
                    choice = (n, r.risk_target / (n * qty_step * quote_usd))
            if choice is None:
                raise RuleViolation(f"smallest position ({n0 * qty_step:g} units) risks "
                                    f"${n0 * qty_step * stop_dist * quote_usd:.2f} - over ${r.risk_max}")
            n, stop_dist = choice
            if max_stop_dist is not None and stop_dist > max_stop_dist * 1.0001:
                raise RuleViolation(f"can't fit ${r.risk_min}-{r.risk_max} risk with this broker's lot size "
                                    f"without a stop wider than the 2.5 ATR maximum")
            qty = n * qty_step
            stop = entry - stop_dist if side == "long" else entry + stop_dist
        else:
            qty = self._round_qty(r.risk_target / (stop_dist * quote_usd), None)
            if qty <= 0:
                raise RuleViolation("position size rounds to zero")

        tp_dist = r.reward_target / (qty * quote_usd)
        tp = entry + tp_dist if side == "long" else entry - tp_dist
        if tp <= 0:
            raise RuleViolation("take-profit price would be <= 0")

        plan = TradePlan(
            symbol=symbol, side=side, qty=qty, entry=entry, stop=stop, take_profit=tp,
            risk_usd=round(qty * stop_dist * quote_usd, 2), reward_usd=round(qty * tp_dist * quote_usd, 2),
            notional=round(qty * entry * quote_usd, 2), quote_usd=quote_usd,
            stop_widened_from=None if abs(stop - original_stop) < 1e-12 else original_stop,
        )
        self.verify(plan)
        return plan

    def verify(self, plan: TradePlan) -> None:
        """Re-check every hard rule. Raises RuleViolation if anything is off."""
        r = self.rules
        if not (r.risk_min <= plan.risk_usd <= r.risk_max):
            raise RuleViolation(f"risk ${plan.risk_usd} outside ${r.risk_min}-${r.risk_max}")
        if not (r.reward_min <= plan.reward_usd <= r.reward_max):
            raise RuleViolation(f"reward ${plan.reward_usd} outside ${r.reward_min}-${r.reward_max}")
        max_notional = r.capital * r.max_leverage
        if plan.notional > max_notional:
            raise RuleViolation(
                f"position ${plan.notional:.2f} > ${max_notional:.2f} buying power "
                f"(stop too tight for this account - would need more leverage)"
            )

    def can_open_new_trade(self, open_positions: int) -> None:
        if open_positions >= self.rules.max_open_trades:
            raise RuleViolation("a trade is already open - only one trade at a time")
