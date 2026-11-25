"""Market data - 4-HOUR candles only.

Sources:
  mt5       - straight from your MetaTrader 5 terminal (same candles as your MT5 chart). Windows only.
  yfinance  - Yahoo Finance 1h data resampled to 4h (free, no account needed)
  csv       - files at data/<SYMBOL>_4h.csv with Date,Open,High,Low,Close,Volume
  synthetic - random generated prices, for testing only
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

COLS = ["Open", "High", "Low", "Close", "Volume"]
TF = pd.Timedelta(hours=4)


# ---------- symbol helpers ----------
def is_forex(symbol: str) -> bool:
    """GBPUSD, GBPUSD=X, and broker variants like GBPUSDm / GBPUSD.r"""
    s = symbol.replace("=X", "")
    return (len(s) >= 6 and s[:6].isalpha() and s[:6].isupper() and "-" not in s
            and (len(s) == 6 or not s[6].isupper()))


def quote_currency(symbol: str) -> str:
    return symbol[3:6].upper() if is_forex(symbol) else "USD"


def pip_size(symbol: str) -> float:
    return 0.01 if "JPY" in symbol.upper() else 0.0001


def yahoo_symbol(symbol: str) -> str:
    """GBPUSD -> GBPUSD=X (Yahoo's forex format); other tickers unchanged."""
    return f"{symbol[:6]}=X" if is_forex(symbol) else symbol


# ---------- helpers ----------
def _clean(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[COLS].dropna(subset=["Open", "High", "Low", "Close"]).copy()
    df["Volume"] = df["Volume"].fillna(0)
    return df


def _to_4h(h1: pd.DataFrame) -> pd.DataFrame:
    if h1.index.tz is None:
        h1.index = h1.index.tz_localize("UTC")
    h1 = h1.tz_convert("UTC")
    out = h1.resample("4h", origin="epoch").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
    return out.dropna(subset=["Open"])


def _utc(ts: pd.Timestamp) -> pd.Timestamp:
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts


def _completed(df: pd.DataFrame) -> pd.DataFrame:
    """Signals use COMPLETED 4h candles only - never a half-formed one."""
    if df.empty:
        return df
    return df.iloc[:-1] if _utc(df.index[-1]) + TF > pd.Timestamp.now(tz="UTC") else df


def _synthetic(symbol: str, n: int) -> pd.DataFrame:
    seed = int(hashlib.md5(symbol.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    fx = is_forex(symbol)
    base = (0.6 + (seed % 100) / 100) if fx else 50 + seed % 500
    vol = (0.006 if fx else 0.02) / np.sqrt(6)  # per 4h candle
    drift = np.repeat(rng.choice([-1, 0, 1], size=n // 120 + 1), 120)[:n] * vol / 10
    close = base * np.exp(np.cumsum(drift + rng.normal(0, vol, n)))
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, vol * 0.1, n))
    hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol * 0.4, n)))
    lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol * 0.4, n)))
    v = np.zeros(n) if fx else rng.lognormal(13, 0.4, n)
    end = pd.Timestamp.now(tz="UTC").floor("4h") - TF
    idx = pd.date_range(end=end, periods=n, freq="4h")
    return pd.DataFrame({"Open": open_, "High": hi, "Low": lo, "Close": close, "Volume": v}, index=idx)


class MarketData:
    def __init__(self, source: str = "yfinance", csv_dir: str = "data"):
        self.source = source
        self.csv_dir = Path(csv_dir)
        self._mt5 = None
        if source == "mt5":
            from . import mt5_link
            self._mt5 = mt5_link

    def _all_bars(self, symbol: str, n: int) -> pd.DataFrame:
        if self.source == "synthetic":
            return _synthetic(symbol, n)
        if self.source == "csv":
            df = pd.read_csv(self.csv_dir / f"{symbol}_4h.csv", parse_dates=["Date"], index_col="Date")
            return _clean(df).tail(n)
        if self.source == "mt5":
            return self._mt5.rates_h4(symbol, n)
        import yfinance as yf
        h1 = yf.Ticker(yahoo_symbol(symbol)).history(period="729d", interval="1h", auto_adjust=True)
        return _to_4h(_clean(h1)).tail(n)

    def bars(self, symbol: str, n: int = 1500, completed_only: bool = True) -> pd.DataFrame:
        """4-hour candles. completed_only drops the candle that is still forming."""
        df = self._all_bars(symbol, n + 1)
        if not completed_only:
            return df
        # MT5's last candle is always the one still forming (its times are broker server time)
        return df.iloc[:-1] if self.source == "mt5" else _completed(df)

    def forming_bar(self, symbol: str) -> pd.Series | None:
        """The 4h candle that is still forming (used only for huge-move detection)."""
        if self.source not in ("yfinance", "mt5"):
            return None
        df = self._all_bars(symbol, 5)
        if df.empty:
            return None
        if self.source == "mt5":
            return df.iloc[-1]
        return df.iloc[-1] if _utc(df.index[-1]) + TF > pd.Timestamp.now(tz="UTC") else None

    def price(self, symbol: str) -> float:
        if self.source == "mt5":
            return self._mt5.mid_price(symbol)
        if self.source == "yfinance":
            import yfinance as yf
            h = yf.Ticker(yahoo_symbol(symbol)).history(period="2d", interval="1h")
            return float(h["Close"].iloc[-1])
        return float(self._all_bars(symbol, 5)["Close"].iloc[-1])

    def quote_usd(self, symbol: str) -> float:
        """USD value of 1 unit of the quote currency. GBPUSD -> 1.0, AUDCHF -> CHF->USD rate."""
        if self.source == "mt5":
            return self._mt5.quote_value_per_unit(symbol)
        q = quote_currency(symbol)
        if q == "USD" or self.source != "yfinance":
            return 1.0
        return self.price(f"{q}USD")
