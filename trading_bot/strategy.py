"""
Strategy: "Trend Pullback + Confluence" on 4-HOUR candles only (swing trading, not scalping).

The bot does not trade on one signal. It lists every reason FOR the trade.
A trade is only considered if:
  * it has an ANCHOR reason (trend alignment for the 4h strategy, or a
    confirmed huge move / breakout for off-schedule trades), and
  * it has at least 3 reasons in total (the 3-reason rule).

Long reasons (shorts are the mirror image):
  1. TREND      price > 200 EMA and 50 EMA > 200 EMA               (anchor)
  2. PULLBACK   price dipped to the 20 EMA in the last 3 candles and closed back above it
  3. MOMENTUM   RSI 40-65 and rising (strength returning, not overbought)
  4. MACD       MACD histogram rising for 2 candles
  5. CANDLE     strong close: green candle closing in the top third of its range
  6. VOLUME     tick volume above 1.2x its 20-candle average
  7. BREAKOUT   close above the prior 20-candle high
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .indicators import add_indicators


@dataclass
class Reason:
    key: str
    text: str


@dataclass
class Setup:
    symbol: str
    side: str            # "long" | "short"
    kind: str            # "strategy_4h" | "huge_move"
    entry: float
    stop: float
    atr: float
    reasons: list[Reason]
    context: dict = field(default_factory=dict)

    @property
    def reason_keys(self) -> set[str]:
        return {r.key for r in self.reasons}


def _f(x: float) -> str:
    return f"{x:,.4f}" if abs(x) < 10 else f"{x:,.2f}"


def _sign(side: str) -> int:
    return 1 if side == "long" else -1


def _stop(df: pd.DataFrame, side: str, entry: float, atr: float, min_atr: float, max_atr: float) -> float:
    """Stop beyond the last 5 candles' swing low/high, clamped to 1-2.5 ATR (4h)."""
    if side == "long":
        dist = entry - (df["Low"].iloc[-5:].min() - 0.2 * atr)
    else:
        dist = (df["High"].iloc[-5:].max() + 0.2 * atr) - entry
    dist = min(max(dist, min_atr * atr), max_atr * atr)
    return entry - dist if side == "long" else entry + dist


def swing_levels(d: pd.DataFrame, entry: float, atr: float, lookback: int = 90, k: int = 3):
    """Nearest swing high above / swing low below entry (pivot = extreme of +-k bars),
    ignoring levels within 0.25 ATR of price (those are noise, not resistance)."""
    w = d.iloc[-lookback:]
    hi, lo = w["High"], w["Low"]
    piv_hi = hi[(hi == hi.rolling(2 * k + 1, center=True).max())]
    piv_lo = lo[(lo == lo.rolling(2 * k + 1, center=True).min())]
    above = piv_hi[piv_hi > entry + 0.25 * atr]
    below = piv_lo[piv_lo < entry - 0.25 * atr]
    return (float(above.min()) if len(above) else None, float(below.max()) if len(below) else None)


def strategy_reasons(d: pd.DataFrame, side: str, price: float) -> list[Reason]:
    row, prev, prev2 = d.iloc[-1], d.iloc[-2], d.iloc[-3]
    last3 = d.iloc[-3:]
    out: list[Reason] = []
    long = side == "long"

    # 1 TREND
    if long and row.Close > row.ema200 and row.ema50 > row.ema200:
        out.append(Reason("trend", f"Uptrend: close {_f(row.Close)} above 200 EMA {_f(row.ema200)}, 50 EMA above 200 EMA."))
    if not long and row.Close < row.ema200 and row.ema50 < row.ema200:
        out.append(Reason("trend", f"Downtrend: close {_f(row.Close)} below 200 EMA {_f(row.ema200)}, 50 EMA below 200 EMA."))

    # 2 PULLBACK to 20 EMA and rejection
    if long and (last3.Low <= last3.ema20 * 1.005).any() and row.Close > row.ema20:
        out.append(Reason("pullback", f"Pulled back to the 20 EMA ({_f(row.ema20)}) in the last 3 candles (4h) and closed back above it."))
    if not long and (last3.High >= last3.ema20 * 0.995).any() and row.Close < row.ema20:
        out.append(Reason("pullback", f"Rallied into the 20 EMA ({_f(row.ema20)}) in the last 3 candles (4h) and closed back below it."))

    # 3 MOMENTUM
    if long and 40 <= row.rsi <= 65 and row.rsi > prev.rsi:
        out.append(Reason("momentum", f"RSI {row.rsi:.1f} rising from the neutral zone (not overbought)."))
    if not long and 35 <= row.rsi <= 60 and row.rsi < prev.rsi:
        out.append(Reason("momentum", f"RSI {row.rsi:.1f} falling from the neutral zone (not oversold)."))

    # 4 MACD
    if long and row.macd_hist > prev.macd_hist > prev2.macd_hist:
        out.append(Reason("macd", "MACD histogram has risen 2 candles in a row (momentum turning up)."))
    if not long and row.macd_hist < prev.macd_hist < prev2.macd_hist:
        out.append(Reason("macd", "MACD histogram has fallen 2 candles in a row (momentum turning down)."))

    # 5 CANDLE
    rng = row.High - row.Low
    if rng > 0:
        pos = (row.Close - row.Low) / rng
        if long and row.Close > row.Open and pos >= 0.66:
            out.append(Reason("candle", f"Strong bullish candle: closed in the top {100 - pos * 100:.0f}% of the candle's range."))
        if not long and row.Close < row.Open and pos <= 0.34:
            out.append(Reason("candle", f"Strong bearish candle: closed in the bottom {pos * 100:.0f}% of the candle's range."))

    # 6 VOLUME (skipped automatically for instruments with no volume data, e.g. forex)
    if row.vol_avg and row.vol_avg > 0 and row.Volume > 1.2 * row.vol_avg:
        if (long and row.Close > row.Open) or (not long and row.Close < row.Open):
            out.append(Reason("volume", f"Volume {row.Volume / row.vol_avg:.1f}x its 20-candle average backs the move."))

    # 7 BREAKOUT
    if long and row.Close > row.high20:
        out.append(Reason("breakout", f"Closed above the prior 20-candle high ({_f(row.high20)})."))
    if not long and row.Close < row.low20:
        out.append(Reason("breakout", f"Closed below the prior 20-candle low ({_f(row.low20)})."))

    return out


def huge_move_reasons(d: pd.DataFrame, today: pd.Series, side: str, price: float) -> list[Reason]:
    """Off-schedule check. `d` = completed 4h candles, `today` = the 4h candle still forming."""
    row = d.iloc[-1]
    move_atr = (price - row.Close) / row.atr
    long = side == "long"
    out: list[Reason] = []

    if (long and move_atr >= 1.5) or (not long and move_atr <= -1.5):
        out.append(Reason("huge_move", f"Huge move: price is {move_atr:+.1f} ATRs (4h) from the last candle's close, in one candle."))
    if long and price > row.high20:
        out.append(Reason("breakout", f"Breaking above the 20-candle high ({_f(row.high20)})."))
    if not long and price < row.low20:
        out.append(Reason("breakout", f"Breaking below the 20-candle low ({_f(row.low20)})."))
    if (long and row.ema50 > row.ema200) or (not long and row.ema50 < row.ema200):
        out.append(Reason("trend", "Move is in the direction of the bigger trend (50 vs 200 EMA)."))
    if row.vol_avg and row.vol_avg > 0 and today is not None and today.Volume > row.vol_avg:
        out.append(Reason("volume", f"This candle's volume already {today.Volume / row.vol_avg:.1f}x the average."))
    if (long and row.rsi < 70) or (not long and row.rsi > 30):
        out.append(Reason("room", f"RSI was {row.rsi:.1f} at the last close, so there's room to run (not already exhausted)."))
    return out


def evaluate(symbol: str, bars: pd.DataFrame, rules, allow_shorts: bool = True,
             today: pd.Series | None = None, live_price: float | None = None) -> tuple[list[Setup], list[str]]:
    """Return (candidate setups, notes about near-misses)."""
    if len(bars) < 210:
        return [], [f"{symbol}: not enough history ({len(bars)} candles, need 210)."]
    d = add_indicators(bars)
    row = d.iloc[-1]
    huge = today is not None and live_price is not None
    sides = ["long", "short"] if allow_shorts else ["long"]
    setups, notes = [], []

    for side in sides:
        if huge:
            reasons = huge_move_reasons(d, today, side, live_price)
            anchor_ok = {"huge_move", "breakout"} <= {r.key for r in reasons}
            entry, kind = live_price, "huge_move"
        else:
            reasons = strategy_reasons(d, side, row.Close)
            anchor_ok = any(r.key == "trend" for r in reasons)
            entry, kind = (live_price or float(row.Close)), "strategy_4h"

        if not reasons:
            continue
        if not anchor_ok or len(reasons) < rules.min_reasons:
            if len(reasons) >= 2:
                notes.append(f"{symbol} {side}: only {len(reasons)} reason(s) "
                             f"({', '.join(r.key for r in reasons)}){'' if anchor_ok else ', no anchor'} - skipped.")
            continue

        stop = _stop(d, side, entry, float(row.atr), rules.min_stop_atr, rules.max_stop_atr)
        res, sup = swing_levels(d, entry, float(row.atr))
        context = {
            "rsi": float(row.rsi), "ema20": float(row.ema20), "atr": float(row.atr),
            "resistance": res, "support": sup, "last_bar": str(d.index[-1]),
        }
        setups.append(Setup(symbol, side, kind, float(entry), float(stop), float(row.atr), reasons, context))
    return setups, notes
