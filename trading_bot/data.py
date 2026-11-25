"""Market data. Default source is Yahoo Finance (free, no API key).

Sources:
  yfinance  - live data from Yahoo Finance (default)
  csv       - files at data/<SYMBOL>_1d.csv with Date,Open,High,Low,Close,Volume
  synthetic - random generated prices, for testing/demo only
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

COLS = ["Open", "High", "Low", "Close", "Volume"]


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[COLS].dropna(subset=["Open", "High", "Low", "Close"])
    df["Volume"] = df["Volume"].fillna(0)
    return df


def _drop_unfinished_daily_bar(df: pd.DataFrame) -> pd.DataFrame:
    """Signals must use COMPLETED daily candles only (no peeking at a half-formed bar)."""
    if df.empty:
        return df
    last = df.index[-1]
    tz = last.tzinfo or timezone.utc
    if last.date() >= datetime.now(tz).date():
        return df.iloc[:-1]
    return df


def _synthetic(symbol: str, n: int, freq: str) -> pd.DataFrame:
    seed = int(hashlib.md5(symbol.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    base = 50 + seed % 500
    # Regime-switching drift so there are trends to find
    drift = np.repeat(rng.choice([-0.002, 0.0, 0.002], size=n // 40 + 1), 40)[:n]
    rets = drift + rng.normal(0, 0.02, n)
    close = base * np.exp(np.cumsum(rets))
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.003, n))
    hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.008, n)))
    lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.008, n)))
    vol = rng.lognormal(13, 0.4, n)
    end = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=1)
    idx = pd.date_range(end=end, periods=n, freq=freq)
    return pd.DataFrame({"Open": open_, "High": hi, "Low": lo, "Close": close, "Volume": vol}, index=idx)


class MarketData:
    def __init__(self, source: str = "yfinance", csv_dir: str = "data"):
        self.source = source
        self.csv_dir = Path(csv_dir)

    def daily(self, symbol: str, completed_only: bool = True, years: int = 2) -> pd.DataFrame:
        if self.source == "synthetic":
            return _synthetic(symbol, 365 * years, "D")
        if self.source == "csv":
            df = pd.read_csv(self.csv_dir / f"{symbol}_1d.csv", parse_dates=["Date"], index_col="Date")
            return _clean(df)
        import yfinance as yf
        df = _clean(yf.Ticker(symbol).history(period=f"{years}y", interval="1d", auto_adjust=True))
        return _drop_unfinished_daily_bar(df) if completed_only else df

    def hourly(self, symbol: str, days: int = 5) -> pd.DataFrame:
        if self.source in ("synthetic", "csv"):
            d = self.daily(symbol).tail(days)
            return d  # coarse stand-in: daily bars
        import yfinance as yf
        return _clean(yf.Ticker(symbol).history(period=f"{days}d", interval="1h", auto_adjust=True))

    def today_bar(self, symbol: str) -> pd.Series | None:
        """Today's (in-progress) daily candle, used only for huge-move detection."""
        if self.source != "yfinance":
            return None
        import yfinance as yf
        df = _clean(yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=True))
        if df.empty:
            return None
        last = df.index[-1]
        if last.date() >= datetime.now(last.tzinfo or timezone.utc).date():
            return df.iloc[-1]
        return None

    def price(self, symbol: str) -> float:
        h = self.hourly(symbol, days=2)
        return float(h["Close"].iloc[-1])
