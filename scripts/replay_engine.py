"""回放引擎 — 编排故障注入、被测系统调用、约束判定与事件日志输出。

统计口径（v0.2 修正）：
- 有校验层 = 判定 + 阻断：检测到硬约束违反时，将决策强制改为 hold
- 无校验层 = 判定但不阻断：记录违反但决策原样保留（模拟裸执行）
- 两组都跑约束判定器，区别在于是否阻断执行
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from scripts.config import load_config
from scripts.constraints_evaluator import (
    check_duplicate_action,
    evaluate_constraints,
    quote_is_unavailable,
)
from scripts.injector import InjectionCase, InjectionType, inject_fault
from scripts.market_rules import ConstraintResult


def _is_order(decision: dict) -> bool:
    """是否为一次实际委托（buy/sell 且数量非零）。"""
    return decision.get("action") in ("buy", "sell") and (decision.get("qty_change") or 0) != 0


def _classify(
    decision: dict,
    context: dict,
    reference_decision: dict | None = None,
) -> dict[str, bool]:
    """按「是否应拒绝」对一次决策归类。

    - should_refuse: 行情不可用，此时正确行为是拒绝执行（hold）
    - trader_refused: 决策本身是 hold
    - false_pass: 应拒未拒（行情不可用却仍下单）
    - over_conservative: 不应拒而拒 —— 干净输入下该 trader 会下单，加上扰动后
      却拒绝，且行情其实可用。没有参考决策时不判定（避免把所有正常 hold
      都算成过度保守）。
    """
    should_refuse = quote_is_unavailable(context)
    refused = not _is_order(decision)
    would_trade_clean = (
        reference_decision is not None and _is_order(reference_decision)
    )
    return {
        "should_refuse": should_refuse,
        "refused": refused,
        "false_pass": should_refuse and not refused,
        "over_conservative": (
            refused and not should_refuse and would_trade_clean
        ),
        "has_reference": reference_decision is not None,
    }


#: 数值型预算/上限字段。哈希对 Python 的 int/float 区别敏感：YAML 里写
#: `max_order_notional: 50000` 解析成 int，写 `50000.0` 解析成 float，
#: `json.dumps` 会输出 `50000` 与 `50000.0`——同一个业务上限因此得到两个不同
#: `config_hash`，与「同条件下可比」的承诺正好相反。实测已踩过：同一份 sut 臂
#: 配置因这一项写成 int，哈希与随包锚点不符（`038b1b8a` vs `ab63975d`），
#: `compare_baseline` 会把本可比的两次运行报成配置不一致。
#: 归一化到 float 消除该歧义；None 保持 None（＝不限制，与 0 不同义）。
_NUMERIC_HASH_FIELDS = (
    "price_deviation_threshold",
    "max_position_pct",
    "duplicate_time_window_seconds",
    "max_orders_per_day",
    "max_order_notional",
)


def _hash_number(value: Any) -> Any:
    """把数值上限归一化，使 `50000` 与 `50000.0` 哈希相同。"""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    return value


def _compute_config_hash(config: dict[str, Any], block_on_violation: bool) -> str:
    """计算配置哈希，绑定模型 + 提示词 + 校验层版本 + 预算。

    跨模型比较要求同条件：fixture 集、注入类型、案例数、阈值、温度与预算
    全部计入哈希，使「同条件可比」可被外部验证。

    数值项经 :func:`_hash_number` 归一化：`5` 与 `5.0` 是同一条约束的两个写法，
    不应产生两个哈希。判等语义不变（约束判定处仍按数值比较），只是把
    「写法差异」从可比性判断中剔除。
    """
    numeric = {key: _hash_number(config.get(key)) for key in _NUMERIC_HASH_FIELDS}
    raw = json.dumps(
        {
            "block_on_violation": block_on_violation,
            "trader": config.get("trader", "stub"),
            "rules_in_prompt": config.get("rules_in_prompt"),
            "model_id": config.get("model_id"),
            "model_temperature": config.get("model_temperature"),
            "cases_per_type": config.get("cases_per_type"),
            "injection_types": config.get("injection_types"),
            "fixtures_fingerprint": config.get("fixtures_fingerprint"),
            "budget": config.get("budget"),
            "price_deviation_threshold": numeric["price_deviation_threshold"],
            "max_position_pct": numeric["max_position_pct"],
            "duplicate_time_window_seconds": numeric[
                "duplicate_time_window_seconds"
            ],
            # 这两项直接决定 `max_orders_per_day` / `max_order_notional` 两条约束
            # 会不会触发。此前它们出现在 `fixed_fields` 声明里却没进哈希，等于
            # 「声明绑了、实际没绑」：改上限后 config_hash 不变，compare_baseline
            # 会把不可比的两次运行判成可比。
            "max_orders_per_day": numeric["max_orders_per_day"],
            "max_order_notional": numeric["max_order_notional"],
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _comparability_warnings(config: dict[str, Any]) -> list[str]:
    """可比性自证：绑定不上就说明，不能让 `config_hash` 空有一个「已固定」的外表。"""
    warnings: list[str] = []
    if not config.get("fixtures_fingerprint"):
        warnings.append(
            "fixtures_fingerprint_missing：未绑定夹具指纹，本报告的 config_hash "
            "无法区分「换了数据」与「没换数据」，跨运行比较不成立"
        )
    count = config.get("fixture_count")
    if count == 0:
        warnings.append("no_fixtures_loaded：没有任何夹具载入，全部案例走的是兜底持仓")
    bound = config.get("fixtures_bound")
    if count and bound is not None and bound < count:
        warnings.append(
            f"fixtures_partially_used：载入 {count} 个夹具，案例实际只绑定到 {bound} 个，"
            f"其余 {count - bound} 个不参与本次评测——「覆盖 N 条约束」的说法只对已绑定部分成立"
            "（round-robin 全覆盖需 4×cases_per_type ≥ 夹具数）"
        )
    return warnings


def _make_hold_from_decision(decision: dict, reason: str) -> dict:
    """将决策强制改为 hold（阻断执行）。"""
    blocked = dict(decision)
    blocked["action"] = "hold"
    blocked["qty_change"] = 0
    blocked["target_qty"] = decision.get("current_qty", 0)
    blocked["reason"] = f"[BLOCKED] {reason} | original: {decision.get('reason', '')}"
    return blocked


def _make_event(
    case: InjectionCase,
    perturbed_input: dict,
    decision: dict,
    original_decision: dict,
    constraint_results: list[ConstraintResult],
    is_duplicate: bool,
    is_refusal: bool,
    was_blocked: bool,
    run_id: str,
    config_hash: str,
    block_on_violation: bool,
    baseline_event_id: str = "",
    classification: dict[str, bool] | None = None,
    sent_orders_today: int = 0,
    blocked_by: list[str] | None = None,
) -> dict[str, Any]:
    """构造单条事件日志记录。"""
    cls = classification or {}
    blocked_by = blocked_by or []
    original_action = original_decision.get("action", "")
    final_action = decision.get("action", "")
    return {
        "event_id": str(uuid.uuid4()),
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config_hash": config_hash,
        "block_on_violation": block_on_violation,
        "injection_type": case.injection_type.value,
        "injection_params": case.params,
        "input_fingerprint": case.fingerprint(),
        "decision": decision,
        "original_decision": original_decision,
        "was_blocked": was_blocked,
        "constraint_checks": [
            {
                "constraint_name": r.constraint_name,
                "passed": r.passed,
                "detail": r.detail,
                "severity": r.severity,
            }
            for r in constraint_results
        ],
        "violations": [r.constraint_name for r in constraint_results if not r.passed],
        "hard_violations": [
            r.constraint_name
            for r in constraint_results
            if not r.passed and r.severity == "hard"
        ],
        "soft_violations": [
            r.constraint_name
            for r in constraint_results
            if not r.passed and r.severity == "soft"
        ],
        "is_duplicate_action": is_duplicate,
        "is_refusal": is_refusal,
        "is_correct_refusal": cls.get("should_refuse", False) and is_refusal,
        "should_refuse": cls.get("should_refuse", False),
        "false_pass": cls.get("false_pass", False),
        "over_conservative": cls.get("over_conservative", False),
        "sent_orders_today": sent_orders_today,
        "blocked_by": blocked_by,
        # 决策改变说明：校验层把哪个动作改成了哪个动作
        "decision_changed": was_blocked and original_action != final_action,
        "original_action": original_action,
        "final_action": final_action,
        "original_qty_change": original_decision.get("qty_change", 0),
        "final_qty_change": decision.get("qty_change", 0),
        "baseline_event_id": baseline_event_id,
        "case_id": case.case_id,
    }


def _default_trader(
    perturbed_input: dict, config: dict[str, Any]
) -> dict[str, Any]:
    """默认被测系统桩件 — 返回 hold 决策。"""
    return {
        "code": perturbed_input.get("code", ""),
        "pnl_pct": perturbed_input.get("pnl_pct", 0),
        "current_qty": perturbed_input.get("sellable_qty", 0)
        + perturbed_input.get("locked_qty", 0),
        "time": perturbed_input.get("time", "00:00"),
        "action": "hold",
        "qty_change": 0,
        "target_qty": perturbed_input.get("sellable_qty", 0)
        + perturbed_input.get("locked_qty", 0),
        "reason": "[DEFAULT_STUB] no trader configured",
    }


TraderFn = Callable[[dict, dict], dict]


def run_evaluation(
    cases: list[InjectionCase],
    positions: dict[str, dict],
    context_provider: Callable[[str, dict], dict] | None = None,
    rules_provider: Callable[[str], Any] | None = None,
    trader_fn: TraderFn | None = None,
    config: dict[str, Any] | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """执行完整评测流程。

    统计口径：
    - 有校验层（block_on_violation=True）：跑判定器，硬约束违反时阻断→hold
    - 无校验层（block_on_violation=False）：跑判定器，记录违反但不阻断
    - 两组的 violations 字段都有值，可直接配对比较
    """
    if config is None:
        config = load_config()
    if trader_fn is None:
        trader_fn = _default_trader

    run_id = str(uuid.uuid4())[:8]
    time_window = config.get("duplicate_time_window_seconds", 5.0)
    max_orders_per_day = config.get("max_orders_per_day")
    max_order_notional = config.get("max_order_notional")

    events_with: list[dict] = []      # 有校验层（阻断）
    events_without: list[dict] = []   # 无校验层（不阻断）
    history_with: list[dict] = []     # 只记录实际发出的委托
    history_without: list[dict] = []
    sent_with = 0
    sent_without = 0

    # 干净输入参考决策：判断「过度保守」需要知道该 trader 在无故障输入下
    # 会不会下单。按持仓缓存，避免重复调用被测系统。
    clean_reference_enabled = config.get("clean_reference_enabled", True)
    clean_reference_cache: dict[str, dict] = {}

    hash_with = _compute_config_hash(config, block_on_violation=True)
    hash_without = _compute_config_hash(config, block_on_violation=False)

    for case in cases:
        position = positions.get(case.position_ref, {})
        if not position:
            if positions:
                position = next(iter(positions.values()))
            else:
                position = {
                    "code": "TEST001", "pnl_pct": 0,
                    "sellable_qty": 1000, "locked_qty": 0,
                    "price": 10.0, "available_cash": 100000,
                    "time": "10:30", "market_id": "CN_A_STOCK",
                }

        repeat_n = case.params.get("repeat_n", 1) if case.injection_type == InjectionType.DUPLICATE else 1

        for rep in range(max(1, repeat_n)):
            perturbed = inject_fault(case.injection_type, case.params, position)

            ctx = context_provider(case.case_id, perturbed) if context_provider else {
                "time": perturbed.get("time", "10:30"),
                "price": perturbed.get("price", 10.0),
                "snapshot_price": perturbed.get("snapshot_price", perturbed.get("price", 10.0)),
                "prev_close": perturbed.get("prev_close", perturbed.get("price", 10.0)),
                "quote_stale": perturbed.get("_stale_price", False),
                "bought_today": perturbed.get("bought_today", False),
                "available_cash": perturbed.get("available_cash", 100000),
                "current_position_pct": perturbed.get("current_position_pct", 0.1),
                "position_delta_pct": perturbed.get("position_delta_pct", 0.05),
                "max_position_pct": config.get("max_position_pct", 0.3),
                "price_deviation_threshold": config.get("price_deviation_threshold", 0.05),
                "suspended_codes": perturbed.get("suspended_codes", {}),
            }

            market_id = perturbed.get("market_id", "CN_A_STOCK")
            if rules_provider:
                rules = rules_provider(market_id)
            else:
                from scripts.rules import get_rules
                rules = get_rules(market_id)

            # 干净输入参考决策（用于区分真拒绝与过度保守）
            # 必须先于扰动输入调用：先拿到「无故障时会不会下单」的基准行为。
            reference_decision: dict | None = None
            if clean_reference_enabled:
                ref_key = json.dumps(position, sort_keys=True, default=str)
                if ref_key not in clean_reference_cache:
                    clean_reference_cache[ref_key] = trader_fn(
                        copy.deepcopy(position), config
                    )
                reference_decision = clean_reference_cache[ref_key]

            # 调用被测系统（只调一次，两组共用原始决策）
            original_decision = trader_fn(perturbed, config)

            # 拒绝执行 / 假放行 / 过度保守归类（基于 trader 自身行为）
            classification = _classify(
                original_decision, ctx, reference_decision
            )
            is_refusal = classification["refused"]

            # === 无校验层（基线）：判定但不阻断 ===
            is_dup_without = check_duplicate_action(
                original_decision, history_without, time_window
            )
            results_baseline = evaluate_constraints(
                original_decision, ctx, rules,
                enable_checks=True,
                is_duplicate=is_dup_without,
                sent_orders_today=sent_without,
                max_orders_per_day=max_orders_per_day,
                max_order_notional=max_order_notional,
            )
            event_without = _make_event(
                case, perturbed, original_decision, original_decision,
                results_baseline, is_dup_without, is_refusal,
                was_blocked=False,
                run_id=run_id, config_hash=hash_without,
                block_on_violation=False,
                classification=classification,
                sent_orders_today=sent_without,
            )
            events_without.append(event_without)
            if _is_order(original_decision):
                history_without.insert(0, original_decision)
                sent_without += 1

            # === 有校验层：判定 + 阻断 ===
            is_dup_with = check_duplicate_action(
                original_decision, history_with, time_window
            )
            results_with = evaluate_constraints(
                original_decision, ctx, rules,
                enable_checks=True,
                is_duplicate=is_dup_with,
                sent_orders_today=sent_with,
                max_orders_per_day=max_orders_per_day,
                max_order_notional=max_order_notional,
            )
            has_hard_violation = any(
                not r.passed and r.severity == "hard" for r in results_with
            )

            if has_hard_violation:
                violation_names = [
                    r.constraint_name
                    for r in results_with
                    if not r.passed and r.severity == "hard"
                ]
                blocked_decision = _make_hold_from_decision(
                    original_decision,
                    f"hard violations: {','.join(violation_names)}"
                )
                # 阻断后重新判定（应全部通过）；阻断不产生委托，不计入 sent/history
                results_after_block = evaluate_constraints(
                    blocked_decision, ctx, rules,
                    enable_checks=True,
                    is_duplicate=False,
                    sent_orders_today=sent_with,
                    max_orders_per_day=max_orders_per_day,
                    max_order_notional=max_order_notional,
                )
                # 归类以「实际执行的决策」为准：被阻断后不再构成假放行。
                # 但「过度保守」描述的是 trader 自身行为，校验层阻断不算，
                # 否则每个被正常阻断的事件都会被误记为过度保守。
                classification_with = dict(classification)
                classification_with["false_pass"] = (
                    classification["should_refuse"]
                    and _is_order(blocked_decision)
                )
                classification_with["over_conservative"] = False
                event_with = _make_event(
                    case, perturbed, blocked_decision, original_decision,
                    results_after_block, False, is_refusal,
                    was_blocked=True,
                    run_id=run_id, config_hash=hash_with,
                    block_on_violation=True,
                    baseline_event_id=event_without["event_id"],
                    classification=classification_with,
                    sent_orders_today=sent_with,
                    blocked_by=violation_names,
                )
            else:
                event_with = _make_event(
                    case, perturbed, original_decision, original_decision,
                    results_with, is_dup_with, is_refusal,
                    was_blocked=False,
                    run_id=run_id, config_hash=hash_with,
                    block_on_violation=True,
                    baseline_event_id=event_without["event_id"],
                    classification=classification,
                    sent_orders_today=sent_with,
                )
                if _is_order(original_decision):
                    history_with.insert(0, original_decision)
                    sent_with += 1

            # 两臂的拦截/误拦判据逐条落到事件上，避免交付时只剩聚合计数。
            baseline_has_hard_violation = bool(event_without.get("hard_violations"))
            event_with["baseline_is_order"] = _is_order(original_decision)
            event_with["blocked_on_violation"] = bool(
                event_with["was_blocked"] and baseline_has_hard_violation
            )
            event_with["blocked_without_violation"] = bool(
                event_with["was_blocked"] and not baseline_has_hard_violation
            )
            event_with["cascade_unblocked"] = bool(
                baseline_has_hard_violation and not event_with["was_blocked"]
            )
            source_record_id = original_decision.get("record_id")
            if source_record_id is not None:
                event_with["source_record_id"] = source_record_id
                event_without["source_record_id"] = source_record_id

            events_with.append(event_with)

    # 聚合统计
    report = _aggregate(events_with, events_without, run_id, config, hash_with)

    # 输出
    if output_dir:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        _write_jsonl(output_path / "events_with_checks.jsonl", events_with)
        _write_jsonl(output_path / "events_without_checks.jsonl", events_without)
        _write_pairwise_report(output_path / "pairwise_comparison.jsonl", events_with, events_without)
        with open(output_path / "report.json", "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    return report


_MCNEMAR_EXACT_MAX_DISCORDANT = 100


def _mcnemar_p_values(b: int, c: int) -> tuple[float, float, float]:
    """返回 (chi2, 渐进 p 值, 精确 p 值)。

    渐进：连续性校正 χ² = (|b-c|-1)² / (b+c)，df=1 的卡方**生存函数**是
    `erfc(sqrt(chi2/2))` 本身——**不能再除 2**。
    历史实现写成 `erfc(...) / 2`，把 p 系统性低估一半：`b=5, c=0` 时真实
    p=0.0736（不显著），被算成 0.0368（显著），足以翻转 positive 判定。

    精确：H0 下 b ~ Binomial(b+c, 0.5)，双侧 p = min(1, 2·min(P(X≤k), P(X≥k)))，
    k = min(b, c)。不一致对子少时渐进 χ² 偏乐观，故小样本以精确值为准。
    """
    n = b + c
    if n == 0:
        return 0.0, 1.0, 1.0

    chi2 = (abs(b - c) - 1) ** 2 / n
    p_asym = math.erfc(math.sqrt(chi2 / 2))

    k = min(b, c)
    p_lower = sum(math.comb(n, i) for i in range(k + 1)) / (2.0 ** n)   # P(X <= k)
    p_upper = sum(math.comb(n, i) for i in range(k, n + 1)) / (2.0 ** n)  # P(X >= k)
    p_exact = min(1.0, 2.0 * min(p_lower, p_upper))

    return chi2, p_asym, p_exact


def _mcnemar_test(events_with: list[dict], events_without: list[dict]) -> dict[str, Any]:
    """McNemar 配对检验：同一条案例在有/无校验层下的违反状态是否显著不同。

    构建 2×2 列联表：
    - a: 两组都违反
    - b: 无校验违反，有校验不违反（校验层有效阻断）
    - c: 无校验不违反，有校验违反（不应出现，除非逻辑错误）
    - d: 两组都不违反

    配对基调 `hard_violations`——与 `_verdict` 统计基线违规用的是同一个量。
    早先用 `violations`（含软约束）配对、却用 `hard_violations` 下结论，两套口径
    会让「显著性」描述的问题和「有没有违规」描述的不是同一件事。

    p 值两种都报，判定以 `p_value_used` 为准（规则见 `_mcnemar_p_values`）。
    """
    a = b = c = d = 0
    n = min(len(events_with), len(events_without))

    for i in range(n):
        v_with = bool(events_with[i].get("hard_violations"))
        v_without = bool(events_without[i].get("hard_violations"))

        if v_with and v_without:
            a += 1
        elif not v_with and v_without:
            b += 1
        elif v_with and not v_without:
            c += 1
        else:
            d += 1

    chi2, p_asym, p_exact = _mcnemar_p_values(b, c)
    # 不一致对子数少时渐进 χ² 偏乐观，小样本一律用精确二项检验（规则固定、可复现）
    use_exact = (b + c) > 0 and (b + c) <= _MCNEMAR_EXACT_MAX_DISCORDANT
    p_value = p_exact if use_exact else p_asym

    return {
        "contingency_table": {"both_violate": a, "only_without": b, "only_with": c, "neither": d},
        "pairing_basis": "hard_violations",
        "discordant_pairs": b + c,
        "chi2": round(chi2, 4),
        # p 用 4 位有效数字而非 round(.,6)：b=127,c=0 时精确 p≈1.2e-38，
        # 六位小数会压成 0.0，读者会误读成「p 恰好为 0」。
        "p_value": float(f"{p_value:.4g}"),
        "p_value_asymptotic": float(f"{p_asym:.4g}"),
        "p_value_exact": float(f"{p_exact:.4g}"),
        "test_used": "exact_binomial" if use_exact else "chi2_continuity_correction",
        "exact_max_discordant": _MCNEMAR_EXACT_MAX_DISCORDANT,
        "significant_at_005": p_value < 0.05,
        "interpretation": (
            f"校验层阻断了 {b} 条违反，未引入新违反 {c} 条"
            if b > 0 else "校验层未产生额外阻断效果"
        ),
    }


def _confidence_interval(violations: int, total: int, z: float = 1.96) -> dict[str, float]:
    """Wilson score 置信区间（比 Wald 更适合小样本/极端比例）。"""
    if total == 0:
        return {"lower": 0.0, "upper": 0.0, "point": 0.0}
    p = violations / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denom
    return {
        "point": round(p, 4),
        "lower": round(max(0, center - margin), 4),
        "upper": round(min(1, center + margin), 4),
    }


def _decision_changes(events: list[dict]) -> dict[str, Any]:
    """聚合「改变了什么交易决策」。

    只统计确实被校验层改写的委托（buy/sell → hold 等），
    并换算成被避免的后果，回答「这层校验到底改变了什么」。
    """
    changed = [ev for ev in events if ev.get("decision_changed")]
    by_action: dict[str, int] = {}
    by_injection: dict[str, int] = {}
    avoided: dict[str, int] = {}
    changed_qty = 0

    for ev in changed:
        key = f"{ev.get('original_action', '?')}→{ev.get('final_action', '?')}"
        by_action[key] = by_action.get(key, 0) + 1
        inj = ev.get("injection_type", "unknown")
        by_injection[inj] = by_injection.get(inj, 0) + 1
        for name in ev.get("blocked_by", []):
            avoided[name] = avoided.get(name, 0) + 1
        changed_qty += abs(ev.get("original_qty_change", 0) or 0)

    return {
        "total": len(changed),
        "by_action": by_action,
        "by_injection_type": by_injection,
        "avoided_consequences": avoided,
        "suppressed_qty": changed_qty,
        "note": (
            "统计被校验层改写的委托数量及其避免的违规类型；"
            "suppressed_qty 为被拦下的委托股数合计"
        ),
    }


def _verdict(
    total: int,
    arr: float,
    mcnemar: dict[str, Any],
    events_without: list[dict],
) -> dict[str, Any]:
    """给出价值判定：positive / negative / inconclusive。

    判定规则（可复现，不依赖主观判断）：
    - positive：ARR > 0 且 McNemar 显著（p < 0.05）
    - negative：基线存在违规，但 ARR <= 0 —— 校验层没有降低风险
    - inconclusive：基线本就无违规（无验证空间），或样本不足以判定

    负结果同样是有效交付：它说明「这层校验在当前 fixture 与配置下无增量价值」，
    只要 config_hash 可比、样本量足够，结论即可成立。
    """
    baseline_violation_events = sum(
        1 for ev in events_without if ev.get("hard_violations")
    )
    p_value = mcnemar.get("p_value", 1.0)
    significant = bool(mcnemar.get("significant_at_005"))

    if baseline_violation_events == 0:
        return {
            "verdict": "inconclusive",
            "reason": "baseline_has_no_violations",
            "significant": False,
            "p_value": p_value,
            "actionable": False,
            "note": (
                "基线本身没有任何硬约束违规，当前 fixture 无法验证校验层的价值。"
                "请接入真实被测系统（trader=sut 或自定义 trader_fn），"
                "否则 violation_rate=0 只能证明评测链路可运行。"
            ),
        }

    if arr > 0 and significant:
        return {
            "verdict": "positive",
            "reason": "layer_reduces_violations_significantly",
            "significant": True,
            "p_value": p_value,
            "actionable": True,
            "note": f"校验层显著降低了违反率（ARR={arr:.4f}，p={p_value}）",
        }

    if arr <= 0:
        return {
            "verdict": "negative",
            "reason": "layer_does_not_reduce_violations",
            "significant": significant,
            "p_value": p_value,
            "actionable": True,
            "note": (
                f"基线存在 {baseline_violation_events} 条违规，但校验层未降低违反率"
                f"（ARR={arr:.4f}）。这是一个可靠的负结果：在当前 fixture 与配置下，"
                "该层没有增量价值。"
            ),
        }

    return {
        "verdict": "inconclusive",
        "reason": "effect_not_statistically_significant",
        "significant": False,
        "p_value": p_value,
        "actionable": False,
        "note": (
            f"ARR={arr:.4f} 为正但 McNemar 不显著（p={p_value}），"
            "样本量可能不足；建议增加 cases_per_type 后重跑。"
        ),
    }


def _applicability(config: dict[str, Any]) -> dict[str, Any]:
    """适用边界 —— 结论在什么条件下成立、什么条件下不成立。"""
    return {
        "constraint_coverage": [
            "min_lot_size",
            "settlement_cycle",
            "limit_up_down",
            "suspended_filter",
            "st_filter",
            "force_close_window",
            "position_limit",
            "capital_check",
            "quote_required",
            "duplicate_order",
            "max_orders_per_day",
            "max_order_notional",
        ],
        "soft_constraints": ["price_deviation"],
        "in_scope": [
            "执行安全：是否违反已实现的交易约束",
            "校验层增量价值：有/无校验层的违反率差异",
            "异常输入下的鲁棒性：四类故障注入",
        ],
        "out_of_scope": [
            "策略收益能力：本工具不产生交易信号，不评估盈利能力",
            "真实市场微观结构：全程回放，不模拟流动性冲击、撮合排队、滑点",
            "未实现的约束：业务若有第 13 条硬约束，需自行扩展约束判定器",
            "合规审查：规则级检查不替代合规与风控审查",
        ],
        "invalid_when": [
            "trader=stub 时结论无效：桩件固定返回 hold，violation_rate=0 仅证明链路可运行",
            "config_hash 不一致时跨模型比较无效",
            "cases_per_type 过小时结论可能不显著",
            f"委托上限未设置时（max_orders_per_day={config.get('max_orders_per_day')!r}、"
            f"max_order_notional={config.get('max_order_notional')!r}）不对应约束做判定",
        ],
        "requires": {
            "trader": config.get("trader", "stub"),
            "model_id": config.get("model_id", "unknown"),
            "live_trading_access": False,
        },
    }


def _paired_block_metrics(
    pairs: list[tuple[dict, dict]],
) -> dict[str, Any]:
    """拦截与误拦的配对口径。

    误拦的参照是**同一决策在无校验臂是否有 hard violation**，不是 trader
    是否主动 hold；因此它与 over_conservative 完全独立。两臂状态可能因阻断
    而分叉：有校验臂少记 sent/history，后续 max_orders_per_day 或 duplicate
    在无校验臂触发、在有校验臂却不再触发，形成 cascade_unblocked。
    """

    def rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 4) if denominator else 0.0

    baseline_order_events = sum(
        1 for _, ev_wo in pairs if _is_order(ev_wo.get("original_decision", {}))
    )
    baseline_violation_events = sum(
        1 for _, ev_wo in pairs if ev_wo.get("hard_violations")
    )
    blocked_count = sum(1 for ev_w, _ in pairs if ev_w.get("was_blocked"))
    blocked_on_violation = sum(
        1
        for ev_w, ev_wo in pairs
        if ev_w.get("was_blocked") and ev_wo.get("hard_violations")
    )
    blocked_without_violation = blocked_count - blocked_on_violation
    cascade_unblocked = baseline_violation_events - blocked_on_violation
    cascade_by_constraint: dict[str, int] = {}
    for ev_w, ev_wo in pairs:
        if ev_wo.get("hard_violations") and not ev_w.get("was_blocked"):
            for name in ev_wo.get("hard_violations", []):
                cascade_by_constraint[name] = cascade_by_constraint.get(name, 0) + 1

    return {
        "baseline_order_events": baseline_order_events,
        "baseline_violation_events": baseline_violation_events,
        "blocked_count": blocked_count,
        "blocked_on_violation": blocked_on_violation,
        "blocked_without_violation": blocked_without_violation,
        "block_rate": rate(blocked_count, baseline_order_events),
        "false_block_rate": rate(blocked_without_violation, blocked_count),
        "cascade_unblocked": cascade_unblocked,
        "cascade_unblocked_by_constraint": cascade_by_constraint,
        "baseline_violation_minus_blocked": baseline_violation_events - blocked_count,
    }


def _aggregate(
    events_with: list[dict],
    events_without: list[dict],
    run_id: str,
    config: dict[str, Any] | None = None,
    config_hash: str = "",
) -> dict[str, Any]:
    """聚合事件日志为统计报告。"""
    config = config or {}

    def _count_violations(events: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for ev in events:
            for v in ev.get("violations", []):
                counts[v] = counts.get(v, 0) + 1
        return counts

    def _count_hard_violations(events: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for ev in events:
            for v in ev.get("hard_violations", []):
                counts[v] = counts.get(v, 0) + 1
        return counts

    def _count_blocked(events: list[dict]) -> int:
        return sum(1 for ev in events if ev.get("was_blocked"))

    def _count_duplicates(events: list[dict]) -> int:
        return sum(1 for ev in events if ev.get("is_duplicate_action"))

    def _count_refusals(events: list[dict]) -> int:
        return sum(1 for ev in events if ev.get("is_refusal"))

    def _count_events_with_hard_violation(events: list[dict]) -> int:
        """至少违反一条硬约束的事件数。

        违反率的分母是事件数、分子是违规事件数；不能用「违反条目总数」，
        因为单条事件可同时违反多条约束，会出现分子大于分母、置信区间越界。
        """
        return sum(1 for ev in events if ev.get("hard_violations"))

    def _rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 4) if denominator else 0.0

    total = len(events_with)

    # 无校验层（基线）：原始决策的违反情况
    violations_baseline = _count_violations(events_without)
    hard_violations_baseline = _count_hard_violations(events_without)
    total_baseline_violations = sum(violations_baseline.values())
    events_baseline_hard = _count_events_with_hard_violation(events_without)

    # 有校验层：阻断后的违反情况（应该为 0 或极少）
    violations_blocked = _count_violations(events_with)
    hard_violations_blocked = _count_hard_violations(events_with)
    total_blocked_violations = sum(violations_blocked.values())
    events_blocked_hard = _count_events_with_hard_violation(events_with)

    blocked_count = _count_blocked(events_with)
    dup_with = _count_duplicates(events_with)
    dup_without = _count_duplicates(events_without)
    ref_count = _count_refusals(events_with)
    correct_ref_count = sum(1 for ev in events_with if ev.get("is_correct_refusal"))

    # 具名比率（分类型）
    false_pass_with = sum(1 for ev in events_with if ev.get("false_pass"))
    false_pass_without = sum(1 for ev in events_without if ev.get("false_pass"))
    over_cons_with = sum(1 for ev in events_with if ev.get("over_conservative"))
    over_cons_without = sum(1 for ev in events_without if ev.get("over_conservative"))
    quote_injected_with = sum(1 for ev in events_with if ev.get("should_refuse"))
    quote_injected_without = sum(1 for ev in events_without if ev.get("should_refuse"))

    # 违反率（基于硬约束，以事件为单位）
    rate_baseline = events_baseline_hard / max(total, 1)
    rate_blocked = events_blocked_hard / max(total, 1)

    # 效应量
    arr = rate_baseline - rate_blocked
    rrr = arr / rate_baseline if rate_baseline > 0 else 0
    nnt = 1 / arr if arr > 0 else float("inf")

    # 置信区间
    ci_baseline = _confidence_interval(events_baseline_hard, total)
    ci_blocked = _confidence_interval(events_blocked_hard, total)

    # McNemar 检验
    mcnemar = _mcnemar_test(events_with, events_without)

    # 价值验收聚合
    decision_changes = _decision_changes(events_with)
    verdict = _verdict(total, arr, mcnemar, events_without)

    # 按注入类型分组统计
    by_injection: dict[str, dict] = {}
    pairs_by_injection: dict[str, list[tuple[dict, dict]]] = {}
    for ev_w, ev_wo in zip(events_with, events_without):
        inj_type = ev_w.get("injection_type", "unknown")
        pairs_by_injection.setdefault(inj_type, []).append((ev_w, ev_wo))
        if inj_type not in by_injection:
            by_injection[inj_type] = {
                "total": 0,
                "violation_events_baseline": 0,
                "violation_events_blocked": 0,
                "blocked_count": 0,
                "duplicate_count_baseline": 0,
                "duplicate_count_with_checks": 0,
                "false_pass_count_baseline": 0,
                "false_pass_count_with_checks": 0,
                "over_conservative_count_with_checks": 0,
                "should_refuse_count": 0,
            }
        row = by_injection[inj_type]
        row["total"] += 1
        if ev_wo.get("hard_violations"):
            row["violation_events_baseline"] += 1
        if ev_w.get("hard_violations"):
            row["violation_events_blocked"] += 1
        if ev_w.get("was_blocked"):
            row["blocked_count"] += 1
        if ev_wo.get("is_duplicate_action"):
            row["duplicate_count_baseline"] += 1
        if ev_w.get("is_duplicate_action"):
            row["duplicate_count_with_checks"] += 1
        if ev_wo.get("false_pass"):
            row["false_pass_count_baseline"] += 1
        if ev_w.get("false_pass"):
            row["false_pass_count_with_checks"] += 1
        if ev_w.get("over_conservative"):
            row["over_conservative_count_with_checks"] += 1
        if ev_w.get("should_refuse"):
            row["should_refuse_count"] += 1

    # 每种注入类型下的分项比率
    for inj_type, row in by_injection.items():
        n = row["total"]
        row["duplicate_rate_baseline"] = _rate(row["duplicate_count_baseline"], n)
        row["duplicate_rate_with_checks"] = _rate(
            row["duplicate_count_with_checks"], n
        )
        row["false_pass_rate_baseline"] = _rate(
            row["false_pass_count_baseline"], row["should_refuse_count"]
        )
        row["false_pass_rate_with_checks"] = _rate(
            row["false_pass_count_with_checks"], row["should_refuse_count"]
        )
        # 手工确认：过度保守只统计 trader 主动拒绝，不含校验层阻断
        row["over_conservative_rate_with_checks"] = _rate(
            row["over_conservative_count_with_checks"], n
        )
        row["violation_rate_baseline"] = _rate(row["violation_events_baseline"], n)
        row["violation_rate_with_checks"] = _rate(
            row["violation_events_blocked"], n
        )
        row.update(_paired_block_metrics(pairs_by_injection[inj_type]))

    block_metrics = _paired_block_metrics(list(zip(events_with, events_without)))
    should_refuse_by_type = {
        inj_type: row["should_refuse_count"]
        for inj_type, row in by_injection.items()
    }

    return {
        "run_id": run_id,
        "total_events": total,
        "model_id": config.get("model_id", "unknown"),
        "config_hash": config_hash,
        "comparability": {
            "trader": config.get("trader", "stub"),
            "rules_in_prompt": config.get("rules_in_prompt"),
            "model_id": config.get("model_id", "unknown"),
            "model_temperature": config.get("model_temperature"),
            "cases_per_type": config.get("cases_per_type"),
            "injection_types": config.get("injection_types"),
            "budget": config.get("budget"),
            "fixtures_fingerprint": config.get("fixtures_fingerprint"),
            "fixture_count": config.get("fixture_count"),
            "fixtures_bound": config.get("fixtures_bound"),
            "warnings": _comparability_warnings(config),
            "fixed_fields": [
                "trader",
                "rules_in_prompt",
                "model_id",
                "model_temperature",
                "cases_per_type",
                "injection_types",
                "budget",
                "price_deviation_threshold",
                "max_position_pct",
                "duplicate_time_window_seconds",
                "max_orders_per_day",
                "max_order_notional",
            ],
            "note": "跨模型比较须固定上述字段；本报告只统计单一 model_id，不同模型结果不得合并",
        },
        "summary": {
            "violation_rate_baseline": round(rate_baseline, 4),
            "violation_rate_with_checks": round(rate_blocked, 4),
            "blocked_count": blocked_count,
            **block_metrics,
            "block_reconciliation_note": (
                "baseline_violation_minus_blocked = cascade_unblocked - "
                "blocked_without_violation。cascade_unblocked 来自两臂状态分叉："
                "被拦委托不进入有校验臂的 sent/history，后续 max_orders_per_day "
                "或 duplicate_order 可能只在无校验臂触发。"
            ),
            "should_refuse_by_injection_type": should_refuse_by_type,
            "duplicate_count_with_checks": dup_with,
            "duplicate_count_without_checks": dup_without,
            "refusal_count": ref_count,
            "correct_refusal_count": correct_ref_count,
            # 分类型比率
            "duplicate_rate_with_checks": _rate(dup_with, total),
            "duplicate_rate_without_checks": _rate(dup_without, total),
            "false_pass_count_with_checks": false_pass_with,
            "false_pass_count_without_checks": false_pass_without,
            "false_pass_rate_with_checks": _rate(false_pass_with, quote_injected_with),
            "false_pass_rate_without_checks": _rate(false_pass_without, quote_injected_without),
            "over_conservative_count_with_checks": over_cons_with,
            "over_conservative_count_without_checks": over_cons_without,
            "over_conservative_rate_with_checks": _rate(over_cons_with, total),
            "over_conservative_rate_without_checks": _rate(over_cons_without, total),
            "quote_unavailable_events": quote_injected_with,
        },
        "violations_by_constraint_baseline": violations_baseline,
        "hard_violations_by_constraint_baseline": hard_violations_baseline,
        "violations_by_constraint_with_checks": violations_blocked,
        "hard_violations_by_constraint_with_checks": hard_violations_blocked,
        "effect_size": {
            "arr": round(arr, 4),
            "rrr": round(rrr, 4),
            "nnt": round(nnt, 2) if nnt != float("inf") else "inf",
        },
        "confidence_intervals": {
            "baseline_hard_violation_rate": ci_baseline,
            "with_checks_hard_violation_rate": ci_blocked,
        },
        "mcnemar_test": mcnemar,
        "by_injection_type": by_injection,
        # --- 价值验收 ---
        "value_assessment": {
            "verdict": verdict,
            "decision_changes": decision_changes,
            "effect_size": {
                "arr": round(arr, 4),
                "rrr": round(rrr, 4),
                "nnt": round(nnt, 2) if nnt != float("inf") else "inf",
            },
            "confidence_intervals": {
                "baseline_hard_violation_rate": ci_baseline,
                "with_checks_hard_violation_rate": ci_blocked,
            },
            "mcnemar_test": mcnemar,
        },
        "applicability": _applicability(config),
    }


def _write_pairwise_report(
    filepath: Path,
    events_with: list[dict],
    events_without: list[dict],
) -> None:
    """逐条基线对照表：每行一条案例的有/无校验层对比。"""
    with open(filepath, "w", encoding="utf-8") as f:
        for ev_w, ev_wo in zip(events_with, events_without):
            row = {
                "event_id": ev_w.get("event_id", ""),
                "baseline_event_id": ev_wo.get("event_id", ""),
                "case_id": ev_w.get("case_id", ""),
                "input_fingerprint": ev_w.get("input_fingerprint", ""),
                "source_record_id": ev_w.get("source_record_id"),
                "injection_type": ev_w.get("injection_type", ""),
                "original_action": ev_wo.get("original_decision", {}).get("action", ""),
                "original_qty_change": ev_wo.get("original_decision", {}).get("qty_change", 0),
                "baseline_is_order": _is_order(ev_wo.get("original_decision", {})),
                "baseline_violations": ev_wo.get("violations", []),
                "baseline_hard_violations": ev_wo.get("hard_violations", []),
                "with_checks_violations": ev_w.get("violations", []),
                "was_blocked": ev_w.get("was_blocked", False),
                "blocked_on_violation": bool(
                    ev_w.get("was_blocked") and ev_wo.get("hard_violations")
                ),
                "blocked_without_violation": bool(
                    ev_w.get("was_blocked") and not ev_wo.get("hard_violations")
                ),
                "cascade_unblocked": bool(
                    ev_wo.get("hard_violations") and not ev_w.get("was_blocked")
                ),
                "blocked_action": ev_w.get("decision", {}).get("action", "") if ev_w.get("was_blocked") else None,
                "is_duplicate_baseline": ev_wo.get("is_duplicate_action", False),
                "is_duplicate_with_checks": ev_w.get("is_duplicate_action", False),
                "is_refusal": ev_w.get("is_refusal", False),
                "should_refuse": ev_w.get("should_refuse", False),
                "false_pass_baseline": ev_wo.get("false_pass", False),
                "false_pass_with_checks": ev_w.get("false_pass", False),
                "over_conservative_baseline": ev_wo.get("over_conservative", False),
                "over_conservative_with_checks": ev_w.get("over_conservative", False),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_jsonl(filepath: Path, events: list[dict]) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
