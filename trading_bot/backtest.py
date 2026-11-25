"""Backtest the exact same strategy + self-review + risk rules on daily history.

Entry at the signal day's close, exits checked on following daily candles
(stop assumed first if SL and TP are both touched in one candle; gaps through
the stop fill at the open). Fixed $ risk per trade, one trade at a time.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from . import config
from .broker import PaperBroker
from .data import MarketData, is_forex
from .review import review
from .risk import RiskGuard, RuleViolation
from .strategy import evaluate


def backtest(symbol: str, source: str = "yfinance", years: int = 5, verbose: bool = True) -> dict:
    rules = config.rules_for_capital(config.STARTING_CAPITAL)
    guard = RiskGuard(rules)
    md = MarketData(source)
    df = md.daily(symbol, years=years)
    quote_usd = md.quote_usd(symbol)  # today's rate, used for the whole test (approximation)
    step = config.FOREX_UNIT_STEP if is_forex(symbol) else None
    tmp = Path(tempfile.mkdtemp()) / "bt.json"
    broker = PaperBroker(tmp, rules.capital)
    skipped_rules = skipped_review = 0

    for i in range(210, len(df)):
        bar = df.iloc[i]
        if broker.position:
            t = broker.check_exit(bar.High, bar.Low, bar.Open, when=df.index[i])
            if t and verbose:
                print(f"{df.index[i].date()} CLOSE {t['side']:5} {t['exit_reason']:18} P&L ${t['pnl']:+.2f}")
            continue
        setups, _ = evaluate(symbol, df.iloc[: i + 1], rules, config.ALLOW_SHORTS)
        for s in setups:
            try:
                plan = guard.size(symbol, s.side, s.entry, s.stop, quote_usd=quote_usd, qty_step=step)
            except RuleViolation:
                skipped_rules += 1
                continue
            rev = review(s, plan, rules, now=df.index[i].to_pydatetime())
            if not rev.approved:
                skipped_review += 1
                continue
            broker.open_position(plan, [r.text for r in s.reasons], when=df.index[i])
            if verbose:
                print(f"{df.index[i].date()} OPEN  {s.side:5} @ {plan.entry:,.2f} SL {plan.stop:,.2f} "
                      f"TP {plan.take_profit:,.2f}  reasons: {', '.join(r.key for r in s.reasons)}")
            break

    closed = broker.state["closed"]
    wins = [t for t in closed if t["pnl"] > 0]
    eq, peak, mdd = rules.capital, rules.capital, 0.0
    for t in closed:
        eq += t["pnl"]
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    res = {
        "symbol": symbol, "trades": len(closed), "wins": len(wins),
        "win_rate": round(100 * len(wins) / len(closed), 1) if closed else 0.0,
        "pnl": round(float(sum(t["pnl"] for t in closed)), 2),
        "worst_loss": float(min((t["pnl"] for t in closed), default=0.0)),
        "max_drawdown": round(mdd, 2),
        "skipped_by_risk_rules": skipped_rules, "vetoed_by_self_review": skipped_review,
    }
    if verbose:
        print("\n" + "\n".join(f"{k:>22}: {v}" for k, v in res.items()))
        print("  (Past results don't guarantee future results. Break-even win rate at ~1.9R is ~35%.)")
    return res
