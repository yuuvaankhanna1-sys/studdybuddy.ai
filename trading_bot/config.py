"""
THE RULES. These are hard limits - every order passes through RiskGuard,
which re-checks all of them right before anything is sent to the broker.

Rules (for a $200 account):
  1. Risk per trade (stop loss)      : $5 - $6
  2. Profit target per trade (TP)    : $9 - $12
  3. Only ONE open trade at a time
  4. No scalping: signals come from DAILY candles, stops are >= 1 daily ATR
  5. Main scan once a day at 09:00; off-schedule only for a "huge move"
  6. 3-reason rule: at least 3 independent reasons, then a self-review
     ("devil's advocate") that can still veto the trade
  7. Every trade (and every skipped setup) is explained in the journal

If you change STARTING_CAPITAL, the $ limits scale proportionally
(2.5%-3% risk, 4.5%-6% reward), so $200 gives exactly $5-6 / $9-12.
"""
from dataclasses import dataclass, field

STARTING_CAPITAL = 200.0

# Timezone for the "9 am" daily scan (IANA name). Change to your own, e.g. "Asia/Kolkata", "Europe/London".
TIMEZONE = "America/New_York"
DAILY_SCAN_HOUR = 9
DAILY_SCAN_MINUTE = 0

# How often (minutes) to check the open trade and look for a huge move.
WATCH_INTERVAL_MIN = 60

# What the bot is allowed to trade (Yahoo Finance tickers). Any asset class works:
# crypto, ETFs, stocks, forex ("EURUSD=X"), commodities ("GC=F").
WATCHLIST = [
    "BTC-USD", "ETH-USD", "SOL-USD",
    "SPY", "QQQ", "NVDA", "TSLA", "AAPL", "AMD",
    "GLD",
]

ALLOW_SHORTS = True


@dataclass(frozen=True)
class RiskRules:
    capital: float = STARTING_CAPITAL
    risk_min: float = 5.0
    risk_max: float = 6.0
    reward_min: float = 9.0
    reward_max: float = 12.0
    max_open_trades: int = 1
    min_reasons: int = 3
    # Position value may not exceed capital * max_leverage. 1.0 = no leverage.
    max_leverage: float = 1.0
    # Anti-scalping: stop must be at least this many DAILY ATRs away.
    min_stop_atr: float = 1.0
    max_stop_atr: float = 2.5
    qty_decimals: int = 6  # fractional quantity precision

    @property
    def risk_target(self) -> float:
        return round((self.risk_min + self.risk_max) / 2, 2)      # $5.50

    @property
    def reward_target(self) -> float:
        return round((self.reward_min + self.reward_max) / 2, 2)  # $10.50

    def validate(self) -> None:
        assert self.capital > 0, "capital must be positive"
        assert 0 < self.risk_min <= self.risk_max, "bad risk range"
        assert 0 < self.reward_min <= self.reward_max, "bad reward range"
        assert self.reward_min > self.risk_max, "reward must exceed risk"
        assert self.max_open_trades == 1, "rule: only one trade at a time"
        assert self.min_reasons >= 3, "rule: at least 3 reasons per trade"
        assert self.min_stop_atr >= 1.0, "rule: no scalping (stop >= 1 daily ATR)"


def rules_for_capital(capital: float = STARTING_CAPITAL) -> RiskRules:
    """$200 -> risk $5-6, reward $9-12. Other sizes scale by the same percentages."""
    scale = capital / 200.0
    r = RiskRules(
        capital=capital,
        risk_min=round(5.0 * scale, 2),
        risk_max=round(6.0 * scale, 2),
        reward_min=round(9.0 * scale, 2),
        reward_max=round(12.0 * scale, 2),
    )
    r.validate()
    return r
