"""
Rule-based swing trading bot - 4-hour candles only.

  python -m trading_bot.bot run        # run forever: 9am scan + hourly watch
  python -m trading_bot.bot scan       # do the 9am scan right now
  python -m trading_bot.bot watch      # manage open trade + look for a huge move now
  python -m trading_bot.bot status     # show account and open trade
  python -m trading_bot.bot backtest --symbol GBPUSD

Add --broker mt5 --source mt5 to trade through your MetaTrader 5 terminal (Windows).
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
    def __init__(self, source: str | None = None, state: str | None = None, journal: str | None = None,
                 broker: str | None = None):
        self.rules = config.rules_for_capital(config.STARTING_CAPITAL)
        self.guard = RiskGuard(self.rules)
        self.data = MarketData(source or config.DATA_SOURCE)
        self.broker_kind = broker or config.BROKER
        if self.broker_kind == "mt5":
            from .mt5_link import MT5Broker
            self.broker = MT5Broker(state or HERE / "state" / "mt5_state.json", self.rules.capital)
        else:
            self.broker = PaperBroker(state or HERE / "state" / "paper_state.json", self.rules.capital)
        self.journal = Journal(journal or HERE / "journal" / "trades.md")
        self.tz = ZoneInfo(config.TIMEZONE)

    # ---------- position management ----------
    def manage_position(self) -> None:
        p = self.broker.position
        if not p:
            return
        if hasattr(self.broker, "sync"):  # MT5 closes at SL/TP by itself; just record the result
            trade = self.broker.sync()
            if trade:
                self.journal.closed(trade, self.broker.state["equity"])
            return
        try:
            bars = self.data.bars(p["symbol"], n=200, completed_only=False)
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

    def _lot_step(self, sym: str) -> tuple[float | None, float | None]:
        """Broker size step in units. MT5 tells us exactly; otherwise use config."""
        if self.broker_kind == "mt5" or self.data.source == "mt5":
            from .mt5_link import lot_info
            _, step, vmin = lot_info(sym)
            return step, vmin
        if is_forex(sym):
            return config.FOREX_UNIT_STEP, config.FOREX_UNIT_STEP
        return None, None

    # ---------- scanning ----------
    def scan(self, huge_move_only: bool = False) -> None:
        self.manage_position()
        label = "huge-move watch (4h)" if huge_move_only else "9am scan (4h candles)"
        if self.broker.open_positions():
            p = self.broker.position
            if not huge_move_only:
                self.journal.note(f"{label}: {p['side']} {p['symbol']} still open - one trade at a time, not scanning.")
            return

        candidates = []
        for sym in config.WATCHLIST:
            try:
                bars = self.data.bars(sym)
                forming = price = None
                if huge_move_only:
                    forming = self.data.forming_bar(sym)
                    if forming is None:
                        continue
                    price = float(forming.Close)
                elif self.data.source in ("yfinance", "mt5"):
                    price = self.data.price(sym)
                setups, notes = evaluate(sym, bars, self.rules, config.ALLOW_SHORTS, today=forming, live_price=price)
            except Exception as e:
                print(f"  {sym}: data error ({e}) - skipped")
                continue
            for n in notes:
                print("  " + n)
            for s in setups:
                try:
                    step, min_qty = self._lot_step(sym)
                    plan = self.guard.size(s.symbol, s.side, s.entry, s.stop,
                                           quote_usd=self.data.quote_usd(sym), qty_step=step, min_qty=min_qty,
                                           max_stop_dist=self.rules.max_stop_atr * s.atr)
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
                self.journal.note(f"{label}: no setup passed the 3-reason rule + self-review. No trade.")
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
        print(f"Bot running on 4h candles. Scan {config.DAILY_SCAN_HOUR:02d}:{config.DAILY_SCAN_MINUTE:02d} "
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
            print(f"OPEN: {p['side']} {p['qty'] / 100_000 if is_forex(p['symbol']) else p['qty']:g}"
                  f"{' lots' if is_forex(p['symbol']) else ''} {p['symbol']} @ {p['entry']:,.5f} "
                  f"SL {p['stop']:,.5f} TP {p['take_profit']:,.5f}")
            for r in p["reasons"]:
                print(f"   - {r}")
        else:
            print("No open trade.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["run", "scan", "watch", "status", "backtest"])
    ap.add_argument("--source", default=config.DATA_SOURCE, choices=["mt5", "yfinance", "csv", "synthetic"])
    ap.add_argument("--broker", default=config.BROKER, choices=["paper", "mt5"])
    ap.add_argument("--symbol", default="GBPUSD", help="backtest symbol")
    args = ap.parse_args()

    if args.command == "backtest":
        from .backtest import backtest
        backtest(args.symbol, args.source)
        return
    bot = Bot(args.source, broker=args.broker)
    {"run": bot.run_forever, "scan": bot.scan, "watch": lambda: bot.scan(huge_move_only=True),
     "status": bot.status}[args.command]()


if __name__ == "__main__":
    main()
