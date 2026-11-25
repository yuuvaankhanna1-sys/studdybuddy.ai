import random
import tempfile
import unittest
from pathlib import Path

from trading_bot.broker import PaperBroker
from trading_bot.config import RiskRules, rules_for_capital
from trading_bot.data import MarketData
from trading_bot.review import review
from trading_bot.risk import RiskGuard, RuleViolation
from trading_bot.strategy import Reason, Setup, evaluate


class TestRiskRules(unittest.TestCase):
    def setUp(self):
        self.rules = rules_for_capital(200)
        self.guard = RiskGuard(self.rules)

    def test_limits_for_200(self):
        self.assertEqual((self.rules.risk_min, self.rules.risk_max), (5.0, 6.0))
        self.assertEqual((self.rules.reward_min, self.rules.reward_max), (9.0, 12.0))

    def test_every_sized_trade_is_within_limits(self):
        rnd = random.Random(1)
        for _ in range(5000):
            entry = rnd.uniform(0.5, 100_000)
            dist = entry * rnd.uniform(0.03, 0.2)
            side = rnd.choice(["long", "short"])
            stop = entry - dist if side == "long" else entry + dist
            try:
                p = self.guard.size("X", side, entry, stop)
            except RuleViolation:
                continue
            self.assertTrue(5.0 <= p.risk_usd <= 6.0, p)
            self.assertTrue(9.0 <= p.reward_usd <= 12.0, p)
            self.assertLessEqual(p.notional, 200.0)

    def test_stop_too_tight_needs_leverage_is_rejected(self):
        with self.assertRaises(RuleViolation):
            self.guard.size("X", "long", 100.0, 99.5)  # $0.50 stop -> $1100 position

    def test_wrong_side_stop_rejected(self):
        with self.assertRaises(RuleViolation):
            self.guard.size("X", "long", 100.0, 101.0)

    def test_one_trade_at_a_time(self):
        self.guard.can_open_new_trade(0)
        with self.assertRaises(RuleViolation):
            self.guard.can_open_new_trade(1)

    def test_rules_cannot_be_loosened(self):
        with self.assertRaises(AssertionError):
            RiskGuard(RiskRules(max_open_trades=2))
        with self.assertRaises(AssertionError):
            RiskGuard(RiskRules(min_reasons=2))
        with self.assertRaises(AssertionError):
            RiskGuard(RiskRules(min_stop_atr=0.3))


class TestBroker(unittest.TestCase):
    def test_stop_first_when_both_hit_and_single_position(self):
        guard = RiskGuard(rules_for_capital(200))
        plan = guard.size("X", "long", 100.0, 95.0)
        b = PaperBroker(Path(tempfile.mkdtemp()) / "s.json", 200)
        b.open_position(plan, ["r"])
        with self.assertRaises(AssertionError):
            b.open_position(plan, ["r"])
        t = b.check_exit(high=150, low=50)
        self.assertEqual(t["exit_reason"], "stop loss")
        self.assertAlmostEqual(t["pnl"], -plan.risk_usd, places=2)


class TestReview(unittest.TestCase):
    def _setup(self, keys):
        return Setup("X", "long", "daily_strategy", 100.0, 95.0, 4.0,
                     [Reason(k, k) for k in keys],
                     {"rsi": 55, "ema20": 99, "atr": 4.0, "resistance": None, "support": None,
                      "last_bar": "2026-01-01"})

    def test_same_kind_reasons_do_not_count_three_times(self):
        rules = rules_for_capital(200)
        s = self._setup(["trend", "momentum", "macd"])  # only 2 families
        plan = RiskGuard(rules).size("X", "long", 100.0, 95.0)
        from datetime import datetime
        self.assertFalse(review(s, plan, rules, now=datetime(2026, 1, 2)).approved)
        s = self._setup(["trend", "momentum", "candle"])
        self.assertTrue(review(s, plan, rules, now=datetime(2026, 1, 2)).approved)


class TestStrategy(unittest.TestCase):
    def test_setups_always_have_3_reasons_and_anchor(self):
        rules = rules_for_capital(200)
        df = MarketData("synthetic").daily("BTC-USD")
        for i in range(250, len(df), 7):
            setups, _ = evaluate("BTC-USD", df.iloc[:i], rules)
            for s in setups:
                self.assertGreaterEqual(len(s.reasons), 3)
                self.assertIn("trend", s.reason_keys)
                self.assertGreaterEqual(abs(s.entry - s.stop), s.atr * 0.999)


if __name__ == "__main__":
    unittest.main()
