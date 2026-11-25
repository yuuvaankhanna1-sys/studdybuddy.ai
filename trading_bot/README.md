# Rule-Based Swing Trading Bot ($200 account)

A **paper-trading** bot that follows strict rules and explains every decision in plain English.

## The rules (enforced in code, can't be switched off)

| Rule | How it's enforced |
|---|---|
| Risk per trade **$5–6** (stop loss) | `RiskGuard` sizes every position to risk $5.50. Any plan outside $5–6 is rejected. |
| Profit target **$9–12** | Take profit is set to +$10.50 (≈1.9 : 1 reward:risk). Rejected if outside $9–12. |
| **One trade at a time** | Checked before scanning and again right before the order. The broker refuses a second position. |
| **No scalping** | Signals use **daily** candles only. The stop must be at least 1 daily ATR away. |
| **Once a day at 9:00** | Main scan runs once a day at 09:00 (`TIMEZONE` in `config.py`). |
| **Huge moves allowed** | Every hour it checks for a move of ≥1.5 daily ATR **plus** a 20-day breakout. It still needs 3+ reasons and has to pass the self-review. |
| **3-reason rule** | It needs at least 3 reasons from **3 different kinds** of evidence (trend / price location / momentum / confirmation), so the same idea said three ways doesn't count. |
| **Check reasoning first** | A self-review argues against the trade (overextended? overbought? resistance in the way of the TP? stale data?). Any failed check means **no trade**. |
| **Explain every trade** | Opens, closes and skipped setups are all written to `journal/trades.md`. |

The rules live in `config.py`. `RiskRules.validate()` won't start the bot if someone loosens them, for example by allowing 2 trades or only 2 reasons.
If you change `STARTING_CAPITAL`, the dollar limits scale by the same percentages (2.5–3% risk, 4.5–6% reward).

## Strategy: Trend Pullback + Confluence (swing, daily candles)

For a **long** trade (shorts are the mirror image), it looks for:
1. **Trend (required):** price above the 200 EMA and the 50 EMA above the 200 EMA
2. **Pullback:** price dipped to the 20 EMA and closed back above it
3. **Momentum:** RSI 40–65 and rising
4. **MACD:** histogram rising 2 days in a row
5. **Candle:** strong green close in the top third of the day's range
6. **Volume:** above 1.2× the 20-day average
7. **Breakout:** close above the prior 20-day high

The stop goes just past the last 5 days' swing low, kept between 1 and 2.5 daily ATR away.
Position size = $5.50 ÷ stop distance. If more than one market qualifies, it takes the one with the most independent reasons.

## Example journal entry
```
## 2026-11-25 14:00 UTC - OPENED SHORT ETH-USD (daily strategy)
Why I took this trade:
  1. Downtrend: close 2,903.02 below 200 EMA 3,242.81, 50 EMA below 200 EMA.
  2. RSI 36.8 falling from the neutral zone (not oversold).
  3. MACD histogram has fallen 2 days in a row (momentum turning down).
  4. Strong bearish candle: closed in the bottom 3% of the day's range.
Self-review before entering:
  [PASS] 3-reason rule: 4 reasons found.
  [PASS] Reasons are independent: 4 different kinds ...
  [PASS] Not a scalp: stop is 1.44 daily ATRs away (min 1.0).
  [PASS] Path to TP: no recent level blocking the target.
  ...
Plan: 0.0187 @ 2,903.02 | SL 3,197.14 (-$5.50) | TP 2,341.50 (+$10.50) | position $54.29
```

## Setup & usage
```bash
pip install -r trading_bot/requirements.txt

python -m trading_bot.bot run       # leave running: 9am scan + hourly watch
python -m trading_bot.bot scan      # run the daily scan right now
python -m trading_bot.bot watch     # check open trade / huge moves right now
python -m trading_bot.bot status    # equity, open trade and its reasons
python -m trading_bot.bot backtest --symbol BTC-USD    # test the same rules on 5 years of data
python -m unittest discover -s trading_bot/tests -t .  # prove the rules hold
```
Edit `WATCHLIST`, `TIMEZONE` and `ALLOW_SHORTS` in `config.py`. It accepts any Yahoo Finance ticker: crypto, stocks, ETFs, forex (`EURUSD=X`), futures (`GC=F`).

## Important notes
- **Paper trading only for now.** Run it for a few weeks and read the journal before risking real money. To go live, add a broker class with the same methods as `PaperBroker`. Use bracket/OCO orders so the stop loss sits at the broker, not just in this script.
- **Why $200 limits which markets qualify:** with no leverage, a $5.50 risk needs a stop at least ~2.75% away (5.50 / 200). Volatile markets like crypto, TSLA and NVDA fit easily. Calm ones like SPY often get rejected with "stop too tight for this account". That's the risk rule doing its job.
- **US stocks at 9:00 New York time** are before the 9:30 open, so the paper fill uses the latest price. Set the scan to 09:45 if you mostly trade US stocks.
- A stop loss can't stop a **gap**. If the price jumps past your stop overnight, the loss can be a bit more than $6. The backtest models this.
- No strategy wins every trade. At ~1.9 : 1 reward:risk, the break-even win rate is about 35%. Past results don't guarantee future results.
