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

    @property
    def rr(self) -> float:
        return self.reward_usd / self.risk_usd


class RiskGuard:
    def __init__(self, rules: RiskRules):
        rules.validate()
        self.rules = rules

    def _round_qty(self, q: float) -> float:
        f = 10 ** self.rules.qty_decimals
        return math.floor(q * f) / f

    def size(self, symbol: str, side: str, entry: float, stop: float) -> TradePlan:
        r = self.rules
        stop_dist = abs(entry - stop)
        if stop_dist <= 0:
            raise RuleViolation("stop must differ from entry")
        if (side == "long" and stop >= entry) or (side == "short" and stop <= entry):
            raise RuleViolation("stop is on the wrong side of entry")

        qty = self._round_qty(r.risk_target / stop_dist)
        if qty <= 0:
            raise RuleViolation("position size rounds to zero")
        tp_dist = r.reward_target / qty
        tp = entry + tp_dist if side == "long" else entry - tp_dist
        if tp <= 0:
            raise RuleViolation("take-profit price would be <= 0")

        plan = TradePlan(
            symbol=symbol, side=side, qty=qty, entry=entry, stop=stop, take_profit=tp,
            risk_usd=round(qty * stop_dist, 2), reward_usd=round(qty * tp_dist, 2),
            notional=round(qty * entry, 2),
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
                f"(stop too tight for this account - would need leverage)"
            )

    def can_open_new_trade(self, open_positions: int) -> None:
        if open_positions >= self.rules.max_open_trades:
            raise RuleViolation("a trade is already open - only one trade at a time")
