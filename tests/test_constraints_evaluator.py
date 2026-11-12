"""约束判定器测试 — ≥15 例覆盖正常/异常/边界。"""

import pytest
from scripts.constraints_evaluator import (
    check_duplicate_action,
    evaluate_constraints,
)
from scripts.rules.a_stock import AStockRules
from scripts.rules.crypto import CryptoRules
from scripts.rules.hk_stock import HKStockRules


class TestMinLotSize:
    def test_valid_lot_size(self, a_stock_context):
        rules = AStockRules()
        decision = {"action": "buy", "qty_change": 200, "code": "600519.SH"}
        results = evaluate_constraints(decision, a_stock_context, rules)
        lot_violations = [r for r in results if r.constraint_name == "min_lot_size"]
        assert len(lot_violations) == 0

    def test_invalid_lot_size(self, a_stock_context):
        rules = AStockRules()
        decision = {"action": "buy", "qty_change": 150, "code": "600519.SH"}
        results = evaluate_constraints(decision, a_stock_context, rules)
        lot_violations = [r for r in results if r.constraint_name == "min_lot_size"]
        assert len(lot_violations) == 1
        assert not lot_violations[0].passed

    def test_zero_qty_no_check(self, a_stock_context):
        rules = AStockRules()
        decision = {"action": "hold", "qty_change": 0, "code": "600519.SH"}
        results = evaluate_constraints(decision, a_stock_context, rules)
        lot_violations = [r for r in results if r.constraint_name == "min_lot_size"]
        assert len(lot_violations) == 0


class TestSettlementCycle:
    def test_t1_violation(self, a_stock_context):
        rules = AStockRules()
        ctx = {**a_stock_context, "bought_today": True}
        decision = {"action": "sell", "qty_change": -100, "code": "000001.SZ"}
        results = evaluate_constraints(decision, ctx, rules)
        t1 = [r for r in results if r.constraint_name == "settlement_cycle"]
        assert len(t1) == 1
        assert not t1[0].passed

    def test_t0_no_violation(self, a_stock_context):
        rules = CryptoRules()
        ctx = {**a_stock_context, "bought_today": True}
        decision = {"action": "sell", "qty_change": -1, "code": "BTCUSDT"}
        results = evaluate_constraints(decision, ctx, rules)
        t1 = [r for r in results if r.constraint_name == "settlement_cycle"]
        assert len(t1) == 0

    def test_not_bought_today_no_violation(self, a_stock_context):
        rules = AStockRules()
        decision = {"action": "sell", "qty_change": -100, "code": "000001.SZ"}
        results = evaluate_constraints(decision, a_stock_context, rules)
        t1 = [r for r in results if r.constraint_name == "settlement_cycle"]
        assert len(t1) == 0


class TestLimitUpDown:
    def test_buy_at_limit_up(self, a_stock_context):
        rules = AStockRules()
        ctx = {**a_stock_context, "prev_close": 10.0}
        decision = {"action": "buy", "qty_change": 100, "code": "600519.SH", "price": 11.0}
        results = evaluate_constraints(decision, ctx, rules)
        limit = [r for r in results if r.constraint_name == "limit_up_down"]
        assert len(limit) == 1

    def test_sell_at_limit_down(self, a_stock_context):
        rules = AStockRules()
        ctx = {**a_stock_context, "prev_close": 10.0}
        decision = {"action": "sell", "qty_change": -100, "code": "600519.SH", "price": 9.0}
        results = evaluate_constraints(decision, ctx, rules)
        limit = [r for r in results if r.constraint_name == "limit_up_down"]
        assert len(limit) == 1

    def test_crypto_no_limit(self, a_stock_context):
        rules = CryptoRules()
        ctx = {**a_stock_context, "prev_close": 67000.0}
        decision = {"action": "buy", "qty_change": 1, "code": "BTCUSDT", "price": 80000.0}
        results = evaluate_constraints(decision, ctx, rules)
        limit = [r for r in results if r.constraint_name == "limit_up_down"]
        assert len(limit) == 0


class TestSuspendedFilter:
    def test_suspended_code(self, a_stock_context):
        rules = AStockRules()
        ctx = {**a_stock_context, "suspended_codes": {"600519.SH": True}}
        decision = {"action": "buy", "qty_change": 100, "code": "600519.SH"}
        results = evaluate_constraints(decision, ctx, rules)
        susp = [r for r in results if r.constraint_name == "suspended_filter"]
        assert len(susp) == 1

    def test_normal_code(self, a_stock_context):
        rules = AStockRules()
        decision = {"action": "buy", "qty_change": 100, "code": "600519.SH"}
        results = evaluate_constraints(decision, a_stock_context, rules)
        susp = [r for r in results if r.constraint_name == "suspended_filter"]
        assert len(susp) == 0


class TestSTFilter:
    def test_st_code(self, a_stock_context):
        rules = AStockRules()
        decision = {"action": "buy", "qty_change": 100, "code": "ST000001"}
        results = evaluate_constraints(decision, a_stock_context, rules)
        st = [r for r in results if r.constraint_name == "st_filter"]
        assert len(st) == 1

    def test_normal_code_no_st(self, a_stock_context):
        rules = AStockRules()
        decision = {"action": "buy", "qty_change": 100, "code": "600519.SH"}
        results = evaluate_constraints(decision, a_stock_context, rules)
        st = [r for r in results if r.constraint_name == "st_filter"]
        assert len(st) == 0


class TestForceCloseWindow:
    def test_buy_after_force_close_time(self, a_stock_context):
        rules = AStockRules()
        ctx = {**a_stock_context, "time": "14:46"}
        decision = {"action": "buy", "qty_change": 100, "code": "600519.SH"}
        results = evaluate_constraints(decision, ctx, rules)
        fc = [r for r in results if r.constraint_name == "force_close_window"]
        assert len(fc) == 1

    def test_hold_after_force_close_no_violation(self, a_stock_context):
        """hold 在强平时间窗内不触发——不产生新委托。"""
        rules = AStockRules()
        ctx = {**a_stock_context, "time": "14:46"}
        decision = {"action": "hold", "qty_change": 0, "code": "600519.SH"}
        results = evaluate_constraints(decision, ctx, rules)
        fc = [r for r in results if r.constraint_name == "force_close_window"]
        assert len(fc) == 0

    def test_before_force_close_time(self, a_stock_context):
        rules = AStockRules()
        decision = {"action": "buy", "qty_change": 100, "code": "600519.SH"}
        results = evaluate_constraints(decision, a_stock_context, rules)
        fc = [r for r in results if r.constraint_name == "force_close_window"]
        assert len(fc) == 0

    def test_hk_different_time(self, a_stock_context):
        rules = HKStockRules()
        ctx = {**a_stock_context, "time": "14:46"}
        decision = {"action": "buy", "qty_change": 100, "code": "00700.HK"}
        results = evaluate_constraints(decision, ctx, rules)
        fc = [r for r in results if r.constraint_name == "force_close_window"]
        assert len(fc) == 0  # HK force close is 15:45


class TestPositionLimit:
    def test_over_limit(self, a_stock_context):
        rules = AStockRules()
        ctx = {**a_stock_context, "current_position_pct": 0.28, "position_delta_pct": 0.05}
        decision = {"action": "buy", "qty_change": 100, "code": "600519.SH"}
        results = evaluate_constraints(decision, ctx, rules)
        pos = [r for r in results if r.constraint_name == "position_limit"]
        assert len(pos) == 1

    def test_under_limit(self, a_stock_context):
        rules = AStockRules()
        ctx = {**a_stock_context, "current_position_pct": 0.1, "position_delta_pct": 0.05}
        decision = {"action": "buy", "qty_change": 100, "code": "600519.SH"}
        results = evaluate_constraints(decision, ctx, rules)
        pos = [r for r in results if r.constraint_name == "position_limit"]
        assert len(pos) == 0


class TestCapitalCheck:
    def test_insufficient_cash(self, a_stock_context):
        rules = AStockRules()
        ctx = {**a_stock_context, "available_cash": 100, "price": 10.0}
        decision = {"action": "buy", "qty_change": 100, "code": "600519.SH", "price": 10.0}
        results = evaluate_constraints(decision, ctx, rules)
        cap = [r for r in results if r.constraint_name == "capital_check"]
        assert len(cap) == 1

    def test_sufficient_cash(self, a_stock_context):
        rules = AStockRules()
        decision = {"action": "buy", "qty_change": 100, "code": "600519.SH", "price": 10.0}
        results = evaluate_constraints(decision, a_stock_context, rules)
        cap = [r for r in results if r.constraint_name == "capital_check"]
        assert len(cap) == 0


class TestPriceDeviation:
    def test_excessive_deviation(self, a_stock_context):
        rules = AStockRules()
        ctx = {**a_stock_context, "snapshot_price": 10.0}
        decision = {"action": "buy", "qty_change": 100, "code": "600519.SH", "price": 12.0}
        results = evaluate_constraints(decision, ctx, rules)
        pd = [r for r in results if r.constraint_name == "price_deviation"]
        assert len(pd) == 1
        assert pd[0].severity == "soft"

    def test_normal_deviation(self, a_stock_context):
        rules = AStockRules()
        decision = {"action": "buy", "qty_change": 100, "code": "600519.SH", "price": 10.2}
        results = evaluate_constraints(decision, a_stock_context, rules)
        pd = [r for r in results if r.constraint_name == "price_deviation"]
        assert len(pd) == 0


class TestEnableChecksToggle:
    def test_disabled_returns_empty(self, a_stock_context):
        rules = AStockRules()
        decision = {"action": "buy", "qty_change": 150, "code": "600519.SH"}
        results = evaluate_constraints(decision, a_stock_context, rules, enable_checks=False)
        assert len(results) == 0


class TestDuplicateAction:
    def test_duplicate_detected(self):
        current = {"code": "600519.SH", "action": "buy", "qty_change": 100}
        history = [{"code": "600519.SH", "action": "buy", "qty_change": 100}]
        assert check_duplicate_action(current, history) is True

    def test_no_duplicate_different_code(self):
        current = {"code": "600519.SH", "action": "buy", "qty_change": 100}
        history = [{"code": "000001.SZ", "action": "buy", "qty_change": 100}]
        assert check_duplicate_action(current, history) is False

    def test_no_duplicate_empty_history(self):
        current = {"code": "600519.SH", "action": "buy", "qty_change": 100}
        assert check_duplicate_action(current, []) is False
