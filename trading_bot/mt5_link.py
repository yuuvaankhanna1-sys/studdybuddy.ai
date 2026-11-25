"""
MetaTrader 5 connection (data + real/demo orders with SL and TP attached).

Requirements (MetaQuotes' official Python package only runs on Windows):
  * Windows PC or Windows VPS with the MT5 desktop terminal installed and logged in
  * pip install MetaTrader5
  * In MT5: Tools > Options > Expert Advisors > tick "Allow algorithmic trading",
    and make sure the "Algo Trading" button in the toolbar is green
Trades the bot opens show up in the MT5 phone app with the SL and TP lines on the chart.

Login: the bot attaches to the terminal that's already logged in. Optionally set
environment variables MT5_LOGIN, MT5_PASSWORD, MT5_SERVER (never put them in code).
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from . import config
from .risk import TradePlan

try:
    import MetaTrader5 as mt5
except ImportError:  # not Windows / not installed
    mt5 = None

_connected = False


def connect() -> None:
    global _connected
    if _connected:
        return
    if mt5 is None:
        raise RuntimeError("MetaTrader5 package not available. It runs only on Windows: pip install MetaTrader5")
    kw = {}
    if os.getenv("MT5_LOGIN"):
        kw = {"login": int(os.environ["MT5_LOGIN"]), "password": os.environ.get("MT5_PASSWORD", ""),
              "server": os.environ.get("MT5_SERVER", "")}
    if not mt5.initialize(**kw):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()} - is the MT5 terminal open and logged in?")
    _connected = True


def broker_symbol(symbol: str) -> str:
    """GBPUSD -> GBPUSD + your broker's suffix (e.g. 'm' -> GBPUSDm)."""
    s = symbol.replace("=X", "") + config.MT5_SYMBOL_SUFFIX
    connect()
    if not mt5.symbol_select(s, True):
        raise RuntimeError(f"symbol {s} not found in MT5 - check MT5_SYMBOL_SUFFIX in config.py")
    return s


def rates_h4(symbol: str, n: int) -> pd.DataFrame:
    s = broker_symbol(symbol)
    r = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_H4, 0, n)
    if r is None or len(r) == 0:
        raise RuntimeError(f"no H4 data for {s}: {mt5.last_error()}")
    df = pd.DataFrame(r)
    df.index = pd.to_datetime(df["time"], unit="s", utc=True)  # broker server time, labelled UTC
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close",
                            "tick_volume": "Volume"})
    return df[["Open", "High", "Low", "Close", "Volume"]].astype(float)


def mid_price(symbol: str) -> float:
    t = mt5.symbol_info_tick(broker_symbol(symbol))
    return (t.bid + t.ask) / 2


def quote_value_per_unit(symbol: str) -> float:
    """Account-currency value of a 1.0 price move per 1 unit, computed by MT5 itself
    (handles AUDCHF: CHF -> account currency)."""
    s = broker_symbol(symbol)
    info = mt5.symbol_info(s)
    p = mid_price(symbol)
    delta = info.point * 1000
    prof = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, s, 1.0, p, p + delta)
    if prof is None:
        raise RuntimeError(f"order_calc_profit failed: {mt5.last_error()}")
    return prof / (info.trade_contract_size * delta)


def lot_info(symbol: str) -> tuple[float, float, float]:
    """(contract size, volume step in units, minimum volume in units)"""
    info = mt5.symbol_info(broker_symbol(symbol))
    cs = info.trade_contract_size
    return cs, info.volume_step * cs, info.volume_min * cs


class MT5Broker:
    """Same interface as PaperBroker, but sends real orders to MT5 (use a DEMO account first!)."""

    MAGIC = 20261125

    def __init__(self, state_path: str | Path, starting_cash: float):
        connect()
        acc = mt5.account_info()
        if acc is None:
            raise RuntimeError("MT5 not logged in to an account")
        if acc.currency != "USD":
            print(f"WARNING: account currency is {acc.currency}; the $ rules are applied in {acc.currency}.")
        print(f"MT5 account {acc.login} on {acc.server} ({'DEMO' if acc.trade_mode == 0 else 'REAL'}), "
              f"balance {acc.balance} {acc.currency}")
        self.path = Path(state_path)
        self.state = json.loads(self.path.read_text()) if self.path.exists() else \
            {"equity": acc.balance, "position": None, "closed": [], "last_daily_scan": None}
        self.state["equity"] = acc.balance
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, indent=2, default=str))

    @property
    def position(self) -> dict | None:
        return self.state["position"]

    def open_positions(self) -> int:
        """Counts EVERY open position on the account (manual ones too) - one trade at a time."""
        return mt5.positions_total()

    def open_position(self, plan: TradePlan, reasons: list[str], when=None) -> dict:
        assert self.open_positions() == 0, "only one trade at a time"
        s = broker_symbol(plan.symbol)
        info = mt5.symbol_info(s)
        tick = mt5.symbol_info_tick(s)
        long = plan.side == "long"
        volume = round(plan.qty / info.trade_contract_size, 2)
        if info.filling_mode & 1:
            filling = mt5.ORDER_FILLING_FOK
        elif info.filling_mode & 2:
            filling = mt5.ORDER_FILLING_IOC
        else:
            filling = mt5.ORDER_FILLING_RETURN
        req = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": s, "volume": volume,
            "type": mt5.ORDER_TYPE_BUY if long else mt5.ORDER_TYPE_SELL,
            "price": tick.ask if long else tick.bid,
            "sl": round(plan.stop, info.digits), "tp": round(plan.take_profit, info.digits),
            "deviation": 20, "magic": self.MAGIC, "comment": "3-reason bot",
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling,
        }
        res = mt5.order_send(req)
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"MT5 order rejected: {getattr(res, 'retcode', None)} {getattr(res, 'comment', mt5.last_error())}")
        pos = asdict(plan) | {"opened_at": str(datetime.now(timezone.utc)), "reasons": reasons,
                              "ticket": res.order, "volume_lots": volume, "fill": res.price}
        self.state["position"] = pos
        self._save()
        return pos

    def sync(self) -> dict | None:
        """MT5 closes trades itself at SL/TP. Detect that and record the result."""
        p = self.state["position"]
        if not p:
            return None
        if mt5.positions_get(ticket=p["ticket"]):
            return None  # still open
        frm = datetime.now(timezone.utc) - timedelta(days=60)
        deals = mt5.history_deals_get(frm, datetime.now(timezone.utc) + timedelta(days=1), position=p["ticket"]) or []
        pnl = round(sum(d.profit + d.swap + d.commission for d in deals), 2)
        exit_px = deals[-1].price if deals else None
        why = "take profit" if pnl > 0 else "stop loss"
        trade = p | {"exit": exit_px, "exit_reason": why, "pnl": pnl, "closed_at": str(datetime.now(timezone.utc))}
        self.state["closed"].append(trade)
        self.state["position"] = None
        self.state["equity"] = mt5.account_info().balance
        self._save()
        return trade
