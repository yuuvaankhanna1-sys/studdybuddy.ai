# Rule-Based Forex Swing Bot ($200 account, 4-hour candles)

Trades **AUDCHF, GBPUSD and EURUSD** on the **4-hour (H4) chart only**. It explains every decision in plain English.
It runs in **paper mode** by default. It can also send orders with the stop loss and take profit attached to your **MetaTrader 5** account. They appear on the MT5 phone chart as the two red SL/TP lines.

## The rules (enforced in code, can't be switched off)

| Rule | How it's enforced |
|---|---|
| Risk per trade **$5–6** (stop loss) | `RiskGuard` sizes every trade to $5–6. Any plan outside that range is rejected. |
| Profit target **$9–12** | Take profit is set to +$10.50. Rejected if outside $9–12. |
| **One trade at a time** | Checked before scanning and again right before the order. With MT5 it counts *every* open position on the account, including ones you opened by hand. |
| **4-hour candles only** | All signals use completed H4 candles. The stop must be ≥ 1 ATR of the 4-hour chart, so it never scalps. The TradingView script refuses to run on any other timeframe. |
| **9:00 scan** | The main scan runs once a day at 09:00 (`TIMEZONE` in `config.py`). |
| **Huge moves allowed** | Every hour it checks the 4-hour candle that's still forming for a move of ≥1.5 ATR **plus** a 20-candle breakout. It still needs 3+ reasons and the self-review. |
| **3-reason rule** | At least 3 reasons from **3 different kinds** of evidence: trend / price location / momentum / candle confirmation. |
| **Check reasoning first** | A self-review argues against the trade: overextended? overbought? a level blocking the TP? stale data? Any fail means **no trade**. |
| **Explain every trade** | Opens, closes and skipped setups are written to `journal/trades.md`. |

## How it sizes a trade with 0.01 lots (MT5)
On GBPUSD/EURUSD, 0.01 lot is about $0.10 per pip. The bot picks the lot count that keeps the risk at $5–6:

| Stop (pips) | Lots | Risk | Take profit for $10.50 |
|---|---|---|---|
| 10 | 0.05 | $5.00 | 21 pips |
| 19 (your GBPUSD chart) | 0.03 | $5.70 | 35 pips |
| 30 | 0.02 | $6.00 | 52.5 pips |
| 40 | 0.01 | stop widened to 55 → $5.50 | 105 pips |

If no lot count fits, it **widens** the stop a little, up to the 2.5 ATR maximum, and says so in the journal. It never tightens a stop.
The TP is always set by **dollars** ($9–12), not by drawing to the next level. On your GBPUSD chart (short at 1.31067, SL 1.31257), the bot would use 0.03 lots with TP **1.30717**, not 1.30181. A TP at 1.30181 would be about $26, which breaks the $9–12 rule.

## Strategy: Trend Pullback + Confluence (4-hour candles)
For a **short** (longs are the mirror image), it looks for:
1. **Trend (required):** price below the 200 EMA and the 50 EMA below the 200 EMA
2. **Pullback:** price rallied into the 20 EMA in the last 3 candles and closed back below it
3. **Momentum:** RSI 35–60 and falling
4. **MACD:** histogram falling 2 candles in a row
5. **Candle:** strong red close in the bottom third of its range
6. **Volume:** tick volume above 1.2× its average (MT5 data only; Yahoo has no forex volume)
7. **Breakout:** close below the prior 20-candle low

The stop goes beyond the last 5 candles' swing high, between 1 and 2.5 ATR away.

## Setup

### Option A – paper trading on any computer (start here)
```bash
pip install -r trading_bot/requirements.txt
python -m trading_bot.bot run       # leave running: 9am scan + hourly watch
python -m trading_bot.bot status    # equity, open trade and its reasons
python -m trading_bot.bot backtest --symbol GBPUSD   # same rules on ~2 years of 4h candles
```

### Option B – trade on your MT5 account (Windows PC or Windows VPS)
The MT5 phone app can't be controlled by other programs. The bot needs the **MT5 desktop terminal** running on Windows, logged in to the same account. Trades it places then show up on your phone with the SL/TP lines.
1. Install MT5 desktop from your broker, log in, and **use a DEMO account first**.
2. In MT5: **Tools → Options → Expert Advisors →** tick **Allow algorithmic trading**. Then make sure the **Algo Trading** button is green.
3. `pip install -r trading_bot/requirements.txt MetaTrader5`
4. If your broker's symbols have a suffix (e.g. `GBPUSDm`), set `MT5_SYMBOL_SUFFIX = "m"` in `config.py`.
5. Run: `python -m trading_bot.bot run --source mt5 --broker mt5`

MT5 closes the trade at SL or TP by itself. The bot records the result, including swap and commission, in the journal.

## Practise on TradingView
`tradingview/three_reason_swing.pine` is the same strategy and the same rules in Pine Script.
1. Open a **4h** chart (GBPUSD, EURUSD, AUDCHF) in TradingView → **Pine Editor** → paste → **Save** → **Add to chart**.
2. **Strategy Tester** shows every past trade, the win rate and the P&L from $200. Each entry has a label listing its reasons, SL/TP in pips, and lot size.
3. Set **"Position size step"** to 1000 for MT5 (0.01 lots) or 1 for brokers that allow any size.
4. **Add alert** on the strategy to get notified, then place the same trade on MT5 demo or TradingView Paper Trading.

## Important notes
- **Leverage:** on 4-hour candles, a $5.50 risk with a 15–40 pip stop means a position of ~$1,500–4,000 on a $200 account (`MAX_LEVERAGE = 30`). Leverage only changes position size; the stop still caps the loss at $5–6. Your broker must allow at least 1:30.
- **Gaps:** a stop loss can't stop a weekend gap. The loss can then be a little over $6. The backtest models this.
- **Swap:** holding forex overnight costs or pays a small daily fee. The backtest ignores it; MT5 results include it.
- **Yahoo vs MT5 candles:** Yahoo 4h candles start at 00/04/08… UTC, while your MT5 chart uses broker server time. Signals can differ slightly. `--source mt5` uses exactly your MT5 candles.
- No strategy wins every trade. At ~1.9 : 1 reward:risk, the break-even win rate is about 35%. Past results don't guarantee future results.
