"""Plain-English trade journal: why each trade was taken (or skipped) and how it ended."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .data import is_forex, pip_size
from .review import Review
from .risk import TradePlan
from .strategy import Setup


class Journal:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, text: str) -> None:
        with self.path.open("a") as f:
            f.write(text + "\n")
        print(text)

    @staticmethod
    def _ts() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def explain_trade(self, setup: Setup, plan: TradePlan, rev: Review) -> None:
        reasons = "\n".join(f"  {i}. {r.text}" for i, r in enumerate(setup.reasons, 1))
        extra = ""
        if is_forex(plan.symbol):
            pip = pip_size(plan.symbol)
            widened = ""
            if plan.stop_widened_from is not None:
                widened = (f"\nStop widened from {plan.stop_widened_from:.5f} to {plan.stop:.5f} so the risk fits "
                           f"$5-6 with 0.01-lot sizes (stops are only ever widened, never tightened).")
            extra = (f"\nForex: {plan.qty / 100_000:.2f} lots ({plan.qty:,.0f} units) | "
                     f"SL {abs(plan.entry - plan.stop) / pip:.0f} pips | TP {abs(plan.take_profit - plan.entry) / pip:.0f} pips | "
                     f"leverage {plan.notional / 200:.1f}x on $200{widened}")
        self._write(
            f"\n## {self._ts()} - OPENED {setup.side.upper()} {setup.symbol} ({setup.kind.replace('_', ' ')})\n"
            f"Why I took this trade:\n{reasons}\n"
            f"Self-review before entering:\n{rev.summary()}\n"
            f"Plan: {plan.qty} @ {plan.entry:,.5f} | SL {plan.stop:,.5f} (-${plan.risk_usd:.2f}) | "
            f"TP {plan.take_profit:,.5f} (+${plan.reward_usd:.2f}) | position ${plan.notional:,.2f}{extra}"
        )

    def explain_skip(self, setup: Setup, why: str, rev: Review | None = None) -> None:
        body = f"\n{rev.summary()}" if rev else ""
        self._write(f"- {self._ts()} SKIPPED {setup.side} {setup.symbol}: {why}{body}")

    def note(self, text: str) -> None:
        self._write(f"- {self._ts()} {text}")

    def closed(self, trade: dict, equity: float) -> None:
        self._write(
            f"\n## {self._ts()} - CLOSED {trade['side'].upper()} {trade['symbol']} by {trade['exit_reason']}\n"
            f"Exit {trade['exit']:,.4f} | P&L ${trade['pnl']:+.2f} | account equity ${equity:,.2f}"
        )
