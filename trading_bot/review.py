"""
Self-review ("check your reasoning before trading").

After a setup has 3+ reasons, the bot argues AGAINST it. Any failed check
vetoes the trade - when in doubt, stay out.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from .risk import TradePlan
from .strategy import Setup

# Reasons in the same family are counted once, so the bot can't pass the
# 3-reason rule by saying the same thing three different ways.
FAMILY = {
    "trend": "trend",
    "pullback": "location", "breakout": "price", "huge_move": "price",
    "momentum": "momentum", "macd": "momentum", "room": "momentum",
    "candle": "confirmation", "volume": "confirmation",
}


@dataclass
class Review:
    approved: bool
    checks: list[tuple[bool, str]] = field(default_factory=list)

    @property
    def score(self) -> int:
        return sum(ok for ok, _ in self.checks)

    def summary(self) -> str:
        return "\n".join(f"  [{'PASS' if ok else 'FAIL'}] {t}" for ok, t in self.checks)


def review(setup: Setup, plan: TradePlan, rules, now: datetime | None = None) -> Review:
    c: list[tuple[bool, str]] = []
    ctx = setup.context
    long = setup.side == "long"
    tp_dist = abs(plan.take_profit - plan.entry)

    # 1. 3-reason rule, with independent reasons
    fams = {FAMILY.get(r.key, r.key) for r in setup.reasons}
    c.append((len(setup.reasons) >= rules.min_reasons,
              f"3-reason rule: {len(setup.reasons)} reasons found."))
    c.append((len(fams) >= 3,
              f"Reasons are independent: {len(fams)} different kinds ({', '.join(sorted(fams))})."))

    # 2. Not a scalp
    stop_atr = abs(plan.entry - plan.stop) / setup.atr
    c.append((stop_atr >= rules.min_stop_atr - 1e-9,
              f"Not a scalp: stop is {stop_atr:.2f} daily ATRs away (min {rules.min_stop_atr})."))

    # 3. Not chasing an overextended move
    ext = abs(plan.entry - ctx["ema20"]) / setup.atr
    limit = 4.0 if setup.kind == "huge_move" else 2.5
    c.append((ext <= limit, f"Not overextended: price is {ext:.1f} ATRs from the 20 EMA (max {limit})."))

    # 4. Not exhausted
    rsi = ctx["rsi"]
    ok = rsi <= 72 if long else rsi >= 28
    c.append((ok, f"Momentum not exhausted: RSI {rsi:.1f} ({'<= 72' if long else '>= 28'} required)."))

    # 5. Clear path to the take-profit
    level = ctx.get("resistance") if long else ctx.get("support")
    if level is not None and ((long and plan.entry < level < plan.take_profit) or
                              (not long and plan.take_profit < level < plan.entry)):
        room = abs(level - plan.entry) / tp_dist
        c.append((room >= 0.5, f"Path to TP: nearest {'resistance' if long else 'support'} {level:,.2f} "
                               f"is {room * 100:.0f}% of the way to TP (need >= 50%)."))
    else:
        c.append((True, "Path to TP: no recent level blocking the target."))

    # 6. Reward:risk and $ limits (RiskGuard already enforced, shown for the record)
    c.append((plan.rr >= 1.5, f"Reward:risk {plan.rr:.2f} (risk ${plan.risk_usd:.2f}, reward ${plan.reward_usd:.2f})."))

    # 7. Fresh data
    now = now or datetime.now(timezone.utc)
    age = (pd.Timestamp(now).date() - pd.Timestamp(ctx["last_bar"]).date()).days
    c.append((age <= 5, f"Data is fresh: last completed candle {ctx['last_bar']} ({age} day(s) old)."))

    return Review(approved=all(ok for ok, _ in c), checks=c)
