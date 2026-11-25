"""Paper broker. Simulates fills and keeps state in a JSON file so restarts are safe.

To go live later, implement the same methods (open_position, check_exit,
open_positions) against your broker's API and use bracket/OCO orders so the
stop loss and take profit sit on the exchange, not just in this script.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .risk import TradePlan


class PaperBroker:
    def __init__(self, state_path: str | Path, starting_cash: float):
        self.path = Path(state_path)
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
        else:
            self.state = {"equity": starting_cash, "position": None, "closed": [], "last_daily_scan": None}
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, indent=2, default=str))

    @property
    def position(self) -> dict | None:
        return self.state["position"]

    def open_positions(self) -> int:
        return 1 if self.state["position"] else 0

    def open_position(self, plan: TradePlan, reasons: list[str], when: datetime | None = None) -> dict:
        assert self.state["position"] is None, "only one trade at a time"
        pos = asdict(plan) | {"opened_at": str(when or datetime.now(timezone.utc)), "reasons": reasons}
        self.state["position"] = pos
        self._save()
        return pos

    def check_exit(self, high: float, low: float, open_: float | None = None,
                   when: datetime | None = None) -> dict | None:
        """Close the position if a candle's range touched SL or TP.
        If both were touched in one candle, assume the stop hit first (conservative)."""
        p = self.state["position"]
        if not p:
            return None
        long = p["side"] == "long"
        exit_px, why = None, None
        # Gap through the stop at the open -> filled at the open (loss can exceed plan; that's real life)
        if open_ is not None and ((long and open_ <= p["stop"]) or (not long and open_ >= p["stop"])):
            exit_px, why = open_, "stop loss (gapped)"
        elif (long and low <= p["stop"]) or (not long and high >= p["stop"]):
            exit_px, why = p["stop"], "stop loss"
        elif (long and high >= p["take_profit"]) or (not long and low <= p["take_profit"]):
            exit_px, why = p["take_profit"], "take profit"
        if exit_px is None:
            return None
        return self._close(exit_px, why, when)

    def _close(self, price: float, why: str, when: datetime | None) -> dict:
        p = self.state["position"]
        sign = 1 if p["side"] == "long" else -1
        pnl = round(float(sign * (price - p["entry"]) * p["qty"] * p.get("quote_usd", 1.0)), 2)
        trade = p | {"exit": price, "exit_reason": why, "pnl": pnl,
                     "closed_at": str(when or datetime.now(timezone.utc))}
        self.state["equity"] = round(self.state["equity"] + pnl, 2)
        self.state["closed"].append(trade)
        self.state["position"] = None
        self._save()
        return trade
