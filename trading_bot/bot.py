"""
Rule-based swing trading bot (paper trading).

  python -m trading_bot.bot run        # run forever: 9am daily scan + hourly watch
  python -m trading_bot.bot scan       # do the daily scan right now
  python -m trading_bot.bot watch      # manage open trade + look for a huge move now
  python -m trading_bot.bot status     # show account and open trade
  python -m trading_bot.bot backtest --symbol BTC-USD
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from . import config
from .broker import PaperBroker
from .data import MarketData, is_forex
from .journal import Journal
from .review import FAMILY, review
from .risk import RiskGuard, RuleViolation
from .strategy import evaluate

HERE = Path(__file__).parent


class Bot:
    def __init__(self, source: str = "yfinance", state: str | None = None, journal: str | None = None):
        self.rules = config.rules_for_capital(config.STARTING_CAPITAL)
        self.guard = RiskGuard(self.rules)
        self.data = MarketData(source)
        self.broker = PaperBroker(state or HERE / "state" / "paper_state.json", self.rules.capital)
        self.journal = Journal(journal or HERE / "journal" / "trades.md")
        self.tz = ZoneInfo(config.TIMEZONE)

    # ---------- position management ----------
    def manage_position(self) -> None:
        p = self.broker.position
        if not p:
            return
        try:
            bars = self.data.hourly(p["symbol"], days=7)
        except Exception as e:  # network hiccup: try again next cycle
            self.journal.note(f"could not fetch {p['symbol']} to manage trade: {e}")
            return
        opened = pd.Timestamp(p["opened_at"])
        if opened.tzinfo is None:
            opened = opened.tz_localize("UTC")
        if bars.index.tz is None:
            opened = opened.tz_convert(None)
        # only candles that started after entry (earlier highs/lows happened before we were in)
        bars = bars[bars.index >= opened]
        for ts, b in bars.iterrows():
            trade = self.broker.check_exit(b.High, b.Low, when=ts)
            if trade:
                self.journal.closed(trade, self.broker.state["equity"])
                return

    # ---------- scanning ----------
    def scan(self, huge_move_only: bool = False) -> None:
        self.manage_position()
        label = "huge-move watch" if huge_move_only else "daily 9am scan"
        if self.broker.open_positions():
            p = self.broker.position
            if not huge_move_only:
                self.journal.note(f"{label}: {p['side']} {p['symbol']} still open - one trade at a time, not scanning.")
            return

        candidates = []
        for sym in config.WATCHLIST:
            try:
                daily = self.data.daily(sym)
                today = price = None
                if huge_move_only:
                    today = self.data.today_bar(sym)
                    if today is None:
                        continue
                    price = float(today.Close)
                elif self.data.source == "yfinance":
                    price = self.data.price(sym)
                setups, notes = evaluate(sym, daily, self.rules, config.ALLOW_SHORTS, today=today, live_price=price)
            except Exception as e:
                print(f"  {sym}: data error ({e}) - skipped")
                continue
            for n in notes:
                print("  " + n)
            for s in setups:
                try:
                    plan = self.guard.size(s.symbol, s.side, s.entry, s.stop,
                                           quote_usd=self.data.quote_usd(sym),
                                           qty_step=config.FOREX_UNIT_STEP if is_forex(sym) else None)
                except RuleViolation as e:
                    self.journal.explain_skip(s, f"breaks a risk rule: {e}")
                    continue
                rev = review(s, plan, self.rules)
                if not rev.approved:
                    self.journal.explain_skip(s, "self-review vetoed it:", rev)
                    continue
                candidates.append((s, plan, rev))

        if not candidates:
            if not huge_move_only:
                self.journal.note(f"{label}: no setup passed the 3-reason rule + self-review. No trade today.")
            return

        # Best setup = most independent reasons, then most reasons, then cleanest review.
        candidates.sort(key=lambda c: (len({FAMILY.get(r.key) for r in c[0].reasons}),
                                       len(c[0].reasons), c[2].score), reverse=True)
        s, plan, rev = candidates[0]

        # Final gate: rules are re-checked one last time right before the order.
        self.guard.can_open_new_trade(self.broker.open_positions())
        self.guard.verify(plan)
        self.broker.open_position(plan, [r.text for r in s.reasons])
        self.journal.explain_trade(s, plan, rev)
        for other, _, _ in candidates[1:]:
            print(f"  (also qualified but not taken - one trade at a time: {other.side} {other.symbol})")

    # ---------- scheduler ----------
    def run_forever(self) -> None:
        print(f"Bot running. Daily scan {config.DAILY_SCAN_HOUR:02d}:{config.DAILY_SCAN_MINUTE:02d} "
              f"{config.TIMEZONE}, watch every {config.WATCH_INTERVAL_MIN} min. Ctrl+C to stop.")
        last_watch = datetime.min.replace(tzinfo=timezone.utc)
        while True:
            now = datetime.now(self.tz)
            today = now.date().isoformat()
            scan_time = now.replace(hour=config.DAILY_SCAN_HOUR, minute=config.DAILY_SCAN_MINUTE,
                                    second=0, microsecond=0)
            try:
                if now >= scan_time and self.broker.state.get("last_daily_scan") != today:
                    self.scan()
                    self.broker.state["last_daily_scan"] = today
                    self.broker._save()
                elif now - last_watch >= timedelta(minutes=config.WATCH_INTERVAL_MIN):
                    last_watch = now
                    self.scan(huge_move_only=True)
            except Exception as e:
                print(f"error during cycle: {e} (will retry)")
            time.sleep(30)

    def status(self) -> None:
        st = self.broker.state
        closed = st["closed"]
        wins = sum(t["pnl"] > 0 for t in closed)
        print(f"Equity: ${st['equity']:,.2f}  (start ${self.rules.capital:,.2f})")
        print(f"Rules: risk ${self.rules.risk_min}-{self.rules.risk_max}, "
              f"reward ${self.rules.reward_min}-{self.rules.reward_max}, 1 trade at a time, 3+ reasons, "
              f"max leverage {self.rules.max_leverage:g}x")
        print(f"Closed trades: {len(closed)}  wins: {wins}  P&L: ${sum(t['pnl'] for t in closed):+.2f}")
        p = st["position"]
        if p:
            print(f"OPEN: {p['side']} {p['qty']} {p['symbol']} @ {p['entry']:,.4f} "
                  f"SL {p['stop']:,.4f} TP {p['take_profit']:,.4f}")
            for r in p["reasons"]:
                print(f"   - {r}")
        else:
            print("No open trade.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["run", "scan", "watch", "status", "backtest"])
    ap.add_argument("--source", default="yfinance", choices=["yfinance", "csv", "synthetic"])
    ap.add_argument("--symbol", default="BTC-USD", help="backtest symbol")
    args = ap.parse_args()

    if args.command == "backtest":
        from .backtest import backtest
        backtest(args.symbol, args.source)
        return
    bot = Bot(args.source)
    {"run": bot.run_forever, "scan": bot.scan, "watch": lambda: bot.scan(huge_move_only=True),
     "status": bot.status}[args.command]()


if __name__ == "__main__":
    main()
