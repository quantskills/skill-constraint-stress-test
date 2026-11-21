"""新增约束与分类型指标的测试。

覆盖此前缺失的部分：
- quote_required（缺行情禁止下单）与假放行率
- duplicate_order（重复委托禁单）与重复动作率
- max_orders_per_day / max_order_notional
- over_conservative 归类
- 阻断后不再重复计入 sent/history
"""

import json

from scripts.constraints_evaluator import (
    check_duplicate_action,
    evaluate_constraints,
    quote_is_unavailable,
)
from scripts.injector import InjectionCase, InjectionType
from scripts.replay_engine import run_evaluation, _aggregate
from scripts.rules import get_rules


POS_OK = {
    "code": "600519.SH", "pnl_pct": 0,
    "sellable_qty": 1000, "locked_qty": 0,
    "price": 10.0, "available_cash": 100000,
    "time": "10:30", "market_id": "CN_A_STOCK",
}


def _ctx(**overrides):
    base = {
        "time": "10:30",
        "price": 10.0,
        "snapshot_price": 10.0,
        "prev_close": 10.0,
        "bought_today": False,
        "available_cash": 100000,
        "current_position_pct": 0.1,
        "position_delta_pct": 0.05,
        "max_position_pct": 0.3,
        "price_deviation_threshold": 0.05,
    }
    base.update(overrides)
    return base


def _buy(qty=100, price=10.0, code="600519.SH"):
    return {
        "code": code, "pnl_pct": 0.01, "current_qty": 1000, "time": "10:30",
        "action": "buy", "qty_change": qty, "target_qty": 1000 + qty,
        "reason": "test", "price": price,
    }


class TestQuoteRequired:
    def test_quote_is_unavailable_detects_none_and_zero(self):
        assert quote_is_unavailable({"price": None}) is True
        assert quote_is_unavailable({"price": 0}) is True
        assert quote_is_unavailable({"price": 10.0}) is False

    def test_quote_is_unavailable_detects_stale(self):
        assert quote_is_unavailable({"price": 10.0, "quote_stale": True}) is True

    def test_missing_key_is_not_unavailable(self):
        assert quote_is_unavailable({}) is False

    def test_order_without_quote_violates(self):
        rules = get_rules("CN_A_STOCK")
        results = evaluate_constraints(_buy(), _ctx(price=None), rules)
        names = [r.constraint_name for r in results if not r.passed]
        assert "quote_required" in names

    def test_hold_without_quote_is_fine(self):
        rules = get_rules("CN_A_STOCK")
        hold = dict(_buy(), action="hold", qty_change=0)
        results = evaluate_constraints(hold, _ctx(price=None), rules)
        assert not any(r.constraint_name == "quote_required" for r in results)


class TestDuplicateOrder:
    def test_duplicate_order_violates(self):
        rules = get_rules("CN_A_STOCK")
        results = evaluate_constraints(_buy(), _ctx(), rules, is_duplicate=True)
        names = [r.constraint_name for r in results if not r.passed]
        assert "duplicate_order" in names

    def test_non_duplicate_does_not_violate(self):
        rules = get_rules("CN_A_STOCK")
        results = evaluate_constraints(_buy(), _ctx(), rules, is_duplicate=False)
        assert not any(r.constraint_name == "duplicate_order" for r in results)

    def test_hold_is_never_duplicate(self):
        hold = dict(_buy(), action="hold", qty_change=0)
        history = [_buy()]
        assert check_duplicate_action(hold, history) is False

    def test_same_order_is_duplicate(self):
        assert check_duplicate_action(_buy(), [_buy()]) is True

    def test_different_qty_is_not_duplicate(self):
        assert check_duplicate_action(_buy(100), [_buy(200)]) is False

    # --- time_window_seconds 必须真的影响判定（早期实现收下不用 = 装饰性旋钮） ---

    def test_same_minute_is_within_default_window(self):
        cur = dict(_buy(), time="10:30")
        assert check_duplicate_action(cur, [dict(_buy(), time="10:30")]) is True

    def test_far_apart_is_outside_default_window(self):
        cur = dict(_buy(), time="10:35")
        assert check_duplicate_action(cur, [dict(_buy(), time="10:30")]) is False

    def test_widening_the_window_changes_the_verdict(self):
        """同一个输入只有窗口不同，结论必须不同——否则旋钮仍是假的。"""
        cur = dict(_buy(), time="10:35")
        hist = [dict(_buy(), time="10:30")]
        assert check_duplicate_action(cur, hist, time_window_seconds=5.0) is False
        assert check_duplicate_action(cur, hist, time_window_seconds=300.0) is True

    def test_missing_time_is_conservatively_in_window(self):
        """没有时间信息不许冒充「不在窗口内」，否则真实重复会被判成不违规。"""
        assert check_duplicate_action(dict(_buy(), time="10:30"), [_buy()]) is True
        assert check_duplicate_action(_buy(), [dict(_buy(), time="10:30")]) is True


class TestOrderCaps:
    def test_daily_order_cap(self):
        rules = get_rules("CN_A_STOCK")
        results = evaluate_constraints(
            _buy(), _ctx(), rules,
            sent_orders_today=5, max_orders_per_day=5,
        )
        names = [r.constraint_name for r in results if not r.passed]
        assert "max_orders_per_day" in names

    def test_daily_order_cap_not_exceeded(self):
        rules = get_rules("CN_A_STOCK")
        results = evaluate_constraints(
            _buy(), _ctx(), rules,
            sent_orders_today=3, max_orders_per_day=5,
        )
        assert not any(r.constraint_name == "max_orders_per_day" for r in results)

    def test_single_order_notional_cap(self):
        rules = get_rules("CN_A_STOCK")
        results = evaluate_constraints(
            _buy(qty=1000, price=10.0), _ctx(), rules,
            max_order_notional=5000.0,
        )
        names = [r.constraint_name for r in results if not r.passed]
        assert "max_order_notional" in names

    def test_notional_within_cap(self):
        rules = get_rules("CN_A_STOCK")
        results = evaluate_constraints(
            _buy(qty=100, price=10.0), _ctx(), rules,
            max_order_notional=5000.0,
        )
        assert not any(r.constraint_name == "max_order_notional" for r in results)


def _trader_respecting_quote(inp, cfg):
    """行情缺失时拒绝，行情正常时下单。"""
    price = inp.get("price")
    if price is None or price == 0:
        return dict(
            _buy(0), action="hold", qty_change=0, target_qty=1000,
            reason="no quote, refuse",
        )
    return _buy(100, price=price)


def _trader_ignoring_quote(inp, cfg):
    """行情缺失仍然下单 —— 假放行。"""
    return _buy(100, price=inp.get("price") or 10.0)


def _missing_quote_case(case_id="mq_0"):
    return InjectionCase(
        case_id=case_id,
        injection_type=InjectionType.MISSING_QUOTE,
        params={"subtype": "feed_disconnect"},
        position_ref="pos_0",
        subtype="feed_disconnect",
    )


class TestFalsePassRate:
    def test_false_pass_detected_for_ignoring_trader(self):
        report = run_evaluation(
            cases=[_missing_quote_case()],
            positions={"pos_0": POS_OK},
            trader_fn=_trader_ignoring_quote,
        )
        s = report["summary"]
        assert s["quote_unavailable_events"] == 1
        assert s["false_pass_count_without_checks"] == 1
        assert s["false_pass_rate_without_checks"] == 1.0

    def test_no_false_pass_for_respecting_trader(self):
        report = run_evaluation(
            cases=[_missing_quote_case()],
            positions={"pos_0": POS_OK},
            trader_fn=_trader_respecting_quote,
        )
        s = report["summary"]
        assert s["false_pass_count_without_checks"] == 0
        assert s["correct_refusal_count"] == 1

    def test_check_layer_blocks_false_pass(self):
        """校验层应在缺行情时阻断下单决策。"""
        report = run_evaluation(
            cases=[_missing_quote_case()],
            positions={"pos_0": POS_OK},
            trader_fn=_trader_ignoring_quote,
        )
        assert report["summary"]["blocked_count"] == 1
        assert report["summary"]["false_pass_count_with_checks"] == 0


class TestDuplicateRate:
    def test_duplicate_orders_blocked_with_checks(self):
        cases = [
            InjectionCase(
                case_id="dup_block",
                injection_type=InjectionType.DUPLICATE,
                params={"repeat_n": 3},
                position_ref="pos_0",
            )
        ]
        report = run_evaluation(
            cases=cases,
            positions={"pos_0": POS_OK},
            trader_fn=lambda inp, cfg: _buy(100),
        )
        s = report["summary"]
        # 第 2、3 次是重复委托
        assert s["duplicate_count_without_checks"] == 2
        # 有校验层时重复委托被阻断
        assert s["duplicate_count_with_checks"] == 0
        assert s["blocked_count"] == 2
        assert "duplicate_order" in report["hard_violations_by_constraint_baseline"]


class TestOverConservative:
    def test_no_over_conservative_without_reference(self):
        """默认桩件永远 hold，但干净输入也是 hold，不应算过度保守。"""
        report = run_evaluation(cases=[_missing_quote_case()], positions={"pos_0": POS_OK})
        assert report["summary"]["over_conservative_count_with_checks"] == 0

    def test_over_conservative_detected(self):
        calls = {"n": 0}

        def flaky(inp, cfg):
            calls["n"] += 1
            if calls["n"] % 2 == 1:      # 干净输入调用：下单
                return _buy(100)
            return dict(_buy(0), action="hold", qty_change=0)  # 扰动输入：莫名拒绝

        report = run_evaluation(
            cases=[
                InjectionCase(
                    case_id="oc",
                    injection_type=InjectionType.PROMPT_PERTURB,
                    params={"strategy": "semantic_equivalent"},
                    position_ref="pos_0",
                )
            ],
            positions={"pos_0": POS_OK},
            trader_fn=flaky,
        )
        assert report["summary"]["over_conservative_count_with_checks"] == 1


class TestSentOrderAccounting:
    def test_blocked_orders_not_counted_or_repeated(self):
        """被阻断的决策不产生委托，不应计入 sent 或成为后续重复依据。"""
        cases = [
            InjectionCase(
                case_id="acct",
                injection_type=InjectionType.DUPLICATE,
                params={"repeat_n": 2},
                position_ref="pos_over",
            )
        ]
        positions = {
            "pos_over": dict(
                POS_OK, current_position_pct=0.29, position_delta_pct=0.5
            )
        }
        report = run_evaluation(
            cases=cases,
            positions=positions,
            trader_fn=lambda inp, cfg: _buy(100),
        )
        # 两次都因仓位上限被阻断，不应出现第二次「重复」判定
        assert report["summary"]["duplicate_count_with_checks"] == 0
        assert report["summary"]["blocked_count"] == 2

class TestCheckLayerNotOverConservative:
    def test_blocked_events_not_counted_as_over_conservative(self):
        """校验层正常阻断不应被误记为「过度保守」。"""
        cases = [
            InjectionCase(
                case_id="block_oc",
                injection_type=InjectionType.DUPLICATE,
                params={"repeat_n": 2},
                position_ref="pos_over",
            )
        ]
        positions = {
            "pos_over": dict(
                POS_OK, current_position_pct=0.29, position_delta_pct=0.5
            )
        }
        report = run_evaluation(
            cases=cases,
            positions=positions,
            trader_fn=lambda inp, cfg: _buy(100),
        )
        s = report["summary"]
        assert s["blocked_count"] == 2
        assert s["over_conservative_count_with_checks"] == 0


class TestByInjectionRates:
    def test_per_type_rates_present(self):
        report = run_evaluation(
            cases=[_missing_quote_case()],
            positions={"pos_0": POS_OK},
            trader_fn=_trader_ignoring_quote,
        )
        row = report["by_injection_type"]["missing_quote"]
        assert row["violation_rate_baseline"] == 1.0
        assert row["violation_rate_with_checks"] == 0.0
        assert row["false_pass_rate_baseline"] == 1.0
        assert row["false_pass_rate_with_checks"] == 0.0

    def test_duplicate_rates_per_type(self):
        cases = [
            InjectionCase(
                case_id="dup_type",
                injection_type=InjectionType.DUPLICATE,
                params={"repeat_n": 3},
                position_ref="pos_0",
            )
        ]
        report = run_evaluation(
            cases=cases,
            positions={"pos_0": POS_OK},
            trader_fn=lambda inp, cfg: _buy(100),
        )
        row = report["by_injection_type"]["duplicate"]
        assert row["total"] == 3
        assert row["duplicate_count_baseline"] == 2
        assert row["duplicate_count_with_checks"] == 0


class TestValueAssessment:
    def test_stub_yields_inconclusive(self):
        """桩件永远 hold，基线无违规，判定应为 inconclusive 而非 positive。"""
        report = run_evaluation(
            cases=[_missing_quote_case()], positions={"pos_0": POS_OK}
        )
        v = report["value_assessment"]["verdict"]
        assert v["verdict"] == "inconclusive"
        assert v["reason"] == "baseline_has_no_violations"
        assert v["actionable"] is False

    def test_real_layer_yields_positive(self):
        # 样本量需足够让 McNemar 显著，单条案例 p=0.5 不足以判定
        cases = [_missing_quote_case(f"mq_{i}") for i in range(10)]
        report = run_evaluation(
            cases=cases,
            positions={"pos_0": POS_OK},
            trader_fn=_trader_ignoring_quote,
        )
        v = report["value_assessment"]["verdict"]
        assert v["verdict"] == "positive"
        assert v["significant"] is True

    def test_small_sample_yields_inconclusive(self):
        report = run_evaluation(
            cases=[_missing_quote_case()],
            positions={"pos_0": POS_OK},
            trader_fn=_trader_ignoring_quote,
        )
        v = report["value_assessment"]["verdict"]
        assert v["verdict"] == "inconclusive"
        assert v["reason"] == "effect_not_statistically_significant"

    def test_decision_changes_aggregated(self):
        cases = [
            InjectionCase(
                case_id="dc",
                injection_type=InjectionType.DUPLICATE,
                params={"repeat_n": 3},
                position_ref="pos_0",
            )
        ]
        report = run_evaluation(
            cases=cases,
            positions={"pos_0": POS_OK},
            trader_fn=lambda inp, cfg: _buy(100),
        )
        dc = report["value_assessment"]["decision_changes"]
        assert dc["total"] == 2            # 第 2、3 次重复被拦
        assert dc["by_action"]["buy→hold"] == 2
        assert dc["by_injection_type"]["duplicate"] == 2
        assert dc["avoided_consequences"]["duplicate_order"] == 2
        assert dc["suppressed_qty"] == 200

    def test_decision_changed_flag_on_events(self):
        cases = [
            InjectionCase(
                case_id="flag",
                injection_type=InjectionType.DUPLICATE,
                params={"repeat_n": 2},
                position_ref="pos_0",
            )
        ]
        report = run_evaluation(
            cases=cases,
            positions={"pos_0": POS_OK},
            trader_fn=lambda inp, cfg: _buy(100),
        )
        assert report["value_assessment"]["decision_changes"]["total"] == 1


class TestApplicability:
    def test_scope_declared(self):
        report = run_evaluation(
            cases=[_missing_quote_case()], positions={"pos_0": POS_OK}
        )
        app = report["applicability"]
        assert "quote_required" in app["constraint_coverage"]
        assert app["requires"]["live_trading_access"] is False
        assert len(app["out_of_scope"]) > 0
        assert len(app["invalid_when"]) > 0

    def test_stub_flagged_invalid(self):
        report = run_evaluation(
            cases=[_missing_quote_case()], positions={"pos_0": POS_OK}
        )
        app = report["applicability"]
        assert app["requires"]["trader"] == "stub"
        assert any("stub" in w for w in app["invalid_when"])


class TestConfigHashDistinguishesTrader:
    def test_different_trader_different_hash(self):
        from scripts.replay_engine import _compute_config_hash
        base = {"model_id": "m", "price_deviation_threshold": 0.05}
        h_stub = _compute_config_hash({**base, "trader": "stub"}, True)
        h_sut = _compute_config_hash({**base, "trader": "sut"}, True)
        assert h_stub != h_sut


class TestVerdictBranches:
    def test_negative_when_layer_does_not_help(self):
        """基线有违规但 ARR<=0 —— 必须判 negative，这是可靠负结果。"""
        from scripts.replay_engine import _verdict

        v = _verdict(
            100, -0.1,
            {"p_value": 0.9, "significant_at_005": False},
            [{"hard_violations": ["x"]}] * 10,
        )
        assert v["verdict"] == "negative"
        assert v["actionable"] is True
        assert "负结果" in v["note"]

    def test_positive_requires_significance(self):
        from scripts.replay_engine import _verdict

        v = _verdict(
            100, 0.2,
            {"p_value": 0.01, "significant_at_005": True},
            [{"hard_violations": ["x"]}] * 10,
        )
        assert v["verdict"] == "positive"

    def test_positive_arr_but_not_significant_is_inconclusive(self):
        from scripts.replay_engine import _verdict

        v = _verdict(
            100, 0.02,
            {"p_value": 0.4, "significant_at_005": False},
            [{"hard_violations": ["x"]}] * 10,
        )
        assert v["verdict"] == "inconclusive"
        assert v["reason"] == "effect_not_statistically_significant"

    def test_no_baseline_violations_is_inconclusive(self):
        from scripts.replay_engine import _verdict

        v = _verdict(100, 0.0, {"p_value": 1.0, "significant_at_005": False}, [])
        assert v["verdict"] == "inconclusive"
        assert v["reason"] == "baseline_has_no_violations"
        assert v["actionable"] is False


class TestConfigHashIncludesRulesPrompt:
    def test_rules_in_prompt_changes_hash(self):
        """「有规则提示」与「无规则提示」是两个不同问题，哈希必须区分。"""
        from scripts.replay_engine import _compute_config_hash

        base = {"model_id": "m", "trader": "llm", "model_temperature": 0}
        h_on = _compute_config_hash({**base, "rules_in_prompt": True}, True)
        h_off = _compute_config_hash({**base, "rules_in_prompt": False}, True)
        assert h_on != h_off


class TestConfigHashIgnoresNumberNotation:
    """同一个业务上限写成 int 还是 float，不该产生两个「不可比」的哈希。

    YAML 里 `max_order_notional: 50000` 解析成 int、`50000.0` 解析成 float，
    `json.dumps` 会原样输出 `50000` 与 `50000.0`。若不归一化，改一个字段的
    写法就会让 `compare_baseline` 把两次本可比的运行判成 incomparable——
    这与哈希「保证同条件可比」的用途正好相反（实测踩过：复跑 sut 臂得
    `038b1b8a`，随包锚点是 `ab63975d`，唯一差别是这一项的类型）。
    """

    def test_int_and_float_notional_hash_identically(self):
        from scripts.replay_engine import _compute_config_hash

        base = {
            "trader": "sut",
            "model_id": "sut-trader",
            "model_temperature": 0,
            "cases_per_type": 60,
            "max_orders_per_day": 8,
        }
        h_int = _compute_config_hash({**base, "max_order_notional": 50000}, True)
        h_float = _compute_config_hash({**base, "max_order_notional": 50000.0}, True)
        assert h_int == h_float

    def test_unlimited_stays_distinct_from_zero(self):
        """`None`（不限制）与 `0`（上限为零）是两条不同规则，哈希必须区分。"""
        from scripts.replay_engine import _compute_config_hash

        base = {"trader": "sut", "model_id": "sut-trader"}
        h_none = _compute_config_hash({**base, "max_orders_per_day": None}, True)
        h_zero = _compute_config_hash({**base, "max_orders_per_day": 0}, True)
        assert h_none != h_zero

    def test_changing_a_real_limit_still_changes_the_hash(self):
        """归一化只能吃掉写法差异，不能吃掉真实配置变更。"""
        from scripts.replay_engine import _compute_config_hash

        base = {"trader": "sut", "model_id": "sut-trader"}
        h_8 = _compute_config_hash({**base, "max_orders_per_day": 8}, True)
        h_9 = _compute_config_hash({**base, "max_orders_per_day": 9}, True)
        assert h_8 != h_9


class TestBlockAndFalseBlockMetrics:
    def test_blocked_violation_metrics_in_summary_and_by_type(self):
        report = run_evaluation(
            cases=[_missing_quote_case()],
            positions={"pos_0": POS_OK},
            trader_fn=_trader_ignoring_quote,
        )
        for row in (report["summary"], report["by_injection_type"]["missing_quote"]):
            assert row["baseline_order_events"] == 1
            assert row["baseline_violation_events"] == 1
            assert row["blocked_on_violation"] == 1
            assert row["blocked_without_violation"] == 0
            assert row["block_rate"] == 1.0
            assert row["false_block_rate"] == 0.0
            assert row["cascade_unblocked"] == 0

    def test_block_rate_denominator_excludes_hold_and_false_pass_is_clean(self):
        cases = [_missing_quote_case("mq_order"), _missing_quote_case("mq_hold")]

        # 用调用序号构造一单、一拒；第二次起 trader 返回 hold。
        calls = {"n": 0}
        def one_order_one_hold(inp, cfg):
            calls["n"] += 1
            if calls["n"] == 1:  # clean reference
                return _buy(100)
            if calls["n"] == 2:
                return _trader_ignoring_quote(inp, cfg)
            return _trader_respecting_quote(inp, cfg)

        report = run_evaluation(cases=cases, positions={"pos_0": POS_OK}, trader_fn=one_order_one_hold)
        row = report["by_injection_type"]["missing_quote"]
        assert report["summary"]["baseline_order_events"] == 1
        assert report["summary"]["block_rate"] == 1.0
        assert row["should_refuse_count"] == 2
        assert row["false_pass_rate_baseline"] == 0.5
        assert report["summary"]["should_refuse_by_injection_type"]["missing_quote"] == 2

    @staticmethod
    def _synthetic_report(ev_w, ev_wo):
        return _aggregate([ev_w], [ev_wo], "synthetic")

    def test_false_block_uses_baseline_violation_as_reference(self):
        ev_w = {
            "injection_type": "duplicate", "was_blocked": True,
            "hard_violations": [], "violations": [], "original_decision": _buy(100),
        }
        ev_wo = {
            "injection_type": "duplicate", "was_blocked": False,
            "hard_violations": [], "violations": [], "original_decision": _buy(100),
        }
        report = self._synthetic_report(ev_w, ev_wo)
        row = report["summary"]
        assert row["blocked_without_violation"] == 1
        assert row["false_block_rate"] == 1.0
        assert row["cascade_unblocked"] == 0
        assert row["baseline_violation_minus_blocked"] == -1
        assert report["by_injection_type"]["duplicate"]["false_block_rate"] == 1.0
        assert "over_conservative_count_with_checks" in row  # 两个口径没有合并

    def test_cascade_reconciles_baseline_minus_blocked(self):
        ev_w = {
            "injection_type": "timeout_retry", "was_blocked": False,
            "hard_violations": [], "violations": [], "original_decision": _buy(100),
        }
        ev_wo = {
            "injection_type": "timeout_retry", "was_blocked": False,
            "hard_violations": ["max_orders_per_day"],
            "violations": ["max_orders_per_day"], "original_decision": _buy(100),
        }
        report = self._synthetic_report(ev_w, ev_wo)
        row = report["summary"]
        assert row["baseline_violation_events"] == 1
        assert row["blocked_count"] == 0
        assert row["cascade_unblocked"] == 1
        assert row["cascade_unblocked_by_constraint"] == {"max_orders_per_day": 1}
        assert row["baseline_violation_minus_blocked"] == 1
        assert "sent/history" in row["block_reconciliation_note"]

    def test_pairwise_file_contains_per_event_block_evidence(self, tmp_path):
        run_evaluation(
            cases=[_missing_quote_case()], positions={"pos_0": POS_OK},
            trader_fn=_trader_ignoring_quote, output_dir=tmp_path,
        )
        row = json.loads((tmp_path / "pairwise_comparison.jsonl").read_text().strip())
        assert row["event_id"] and row["baseline_event_id"]
        assert row["input_fingerprint"]
        assert row["baseline_is_order"] is True
        assert row["blocked_on_violation"] is True
        assert row["blocked_without_violation"] is False
        assert row["cascade_unblocked"] is False
