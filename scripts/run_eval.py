"""主入口 CLI — 约束压力测试评测编排器。

CLI 错误契约：stdout 只输出一个纯 JSON 对象，退出码区分 0/1/2/130。
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
# 让 `from scripts.xxx import ...` 在「直接运行脚本」和「作为包导入」两种方式下都可用
for _p in (str(REPO_ROOT), str(REPO_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class InputValidationError(ValueError):
    """输入校验失败。code 用于机器消费方分支处理。"""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


def _emit(payload: Dict[str, Any], exit_code: int) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return exit_code


def validate_input(cases_dir: str, config_path: str) -> None:
    """校验输入参数合法性。"""
    cases_path = Path(cases_dir)
    if not cases_path.exists():
        raise InputValidationError(
            "cases_dir_not_found",
            f"Cases directory not found: {cases_dir}",
            hint=f"Create directory with injection case fixtures",
        )
    config_p = Path(config_path)
    if not config_p.exists():
        # 配置文件可选，使用默认值
        pass


def run(
    cases_dir: str | None = None,
    config_path: str | None = None,
    output_dir: str | None = None,
    market_id: str | None = None,
    injection_types: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """执行约束压力测试评测。

    Args:
        cases_dir: fixture 目录路径
        config_path: 评测配置文件路径
        output_dir: 结果输出目录
        market_id: 限定评测市场
        injection_types: 限定故障类型

    Returns:
        聚合统计报告 dict

    Raises:
        InputValidationError: 输入校验失败
    """
    from scripts.config import load_config
    from scripts.injector import (
        generate_duplicate_cases,
        generate_missing_quote_cases,
        generate_prompt_perturb_cases,
        generate_timeout_retry_cases,
        bind_machine_refs,
)
    from scripts.replay_engine import run_evaluation

    if cases_dir is None:
        cases_dir = str(REPO_ROOT / "fixtures")
    if config_path is None:
        config_path = str(REPO_ROOT / "config" / "eval_config.yaml")
    if output_dir is None:
        output_dir = str(REPO_ROOT / "output")

    validate_input(cases_dir, config_path)

    cli_overrides: dict[str, Any] = {}
    if output_dir:
        cli_overrides["output_dir"] = output_dir
    config = load_config(config_path, cli_overrides)

    cases_per_type = config.get("cases_per_type", 30)

    # 选择被测系统（必须先于案例生成：agent 模式需用它记录的导出参数对齐案例集）
    trader_name = config.get("trader", "stub")
    trader_fn = None
    if trader_name == "sut":
        from scripts.sut_adapter import sut_trader
        trader_fn = sut_trader
    elif trader_name == "llm":
        raise InputValidationError(
            "trader_llm_removed",
            "trader=llm（skill 内部直连 LLM 代理）已移除；真实模型评测改走 agent 模式，"
            "由阅读本 skill 的 agent 亲自作答",
            hint="python3 scripts/agent_mode.py export → 填写 decisions → "
                 "python3 scripts/agent_mode.py replay --decisions <file>",
        )
    elif trader_name == "agent":
        import os as _os
        from scripts.agent_mode import AgentModeError, decisions_trader
        dec_path = _os.environ.get("CONSTRAINT_AGENT_DECISIONS", "")
        if not dec_path:
            raise InputValidationError(
                "decisions_not_found",
                "trader=agent 需要 --decisions 或 CONSTRAINT_AGENT_DECISIONS",
                hint="先用 scripts/agent_mode.py export 导出清单，填好后 replay",
            )
        try:
            trader_fn = decisions_trader(dec_path)
        except AgentModeError as e:
            raise InputValidationError(e.code, e.message, e.hint) from e
        # agent 模式下不发起干净输入参考调用：清单里没有这一条，
        # 它会被记成未作答，反而污染统计。过度保守判定在 agent 模式不适用。
        config["clean_reference_enabled"] = False
        # 案例集必须与导出时完全一致，否则清单对不上、大量条目会「未作答」
        params = getattr(trader_fn, "__agent_params__", {}) or {}
        if params.get("cases_per_type"):
            cases_per_type = int(params["cases_per_type"])
            config["cases_per_type"] = cases_per_type
        if params.get("injection_types") and not injection_types:
            injection_types = list(params["injection_types"])
    elif trader_name != "stub":
        raise InputValidationError(
            "unknown_trader",
            f"未知被测系统：{trader_name}",
            hint=(
                "可选值：stub（内置桩件）/ sut（本地被测系统）/ "
                "llm（真实大模型）/ agent（由阅读 skill 的 AI 自行决策）"
            ),
        )

    # 生成注入案例
    all_cases = []
    # CLI 参数 > 配置文件/环境变量 > 默认四类。此前配置文件里的 injection_types
    # 只进 config_hash、不参与案例选择——改它等于只改哈希不改行为。
    active_types = (
        injection_types
        or config.get("injection_types")
        or [
            "duplicate",
            "prompt_perturb",
            "missing_quote",
            "timeout_retry",
        ]
    )

    if "duplicate" in active_types:
        all_cases.extend(generate_duplicate_cases({}, cases_per_type))
    if "prompt_perturb" in active_types:
        all_cases.extend(generate_prompt_perturb_cases(cases_per_type))
    if "missing_quote" in active_types:
        all_cases.extend(generate_missing_quote_cases(cases_per_type))
    if "timeout_retry" in active_types:
        all_cases.extend(generate_timeout_retry_cases(cases_per_type))

    if not all_cases:
        raise InputValidationError(
            "no_cases",
            "No injection cases generated",
            hint="Check injection_types parameter",
        )

    # 加载 positions
    positions: dict[str, dict] = {}
    position_origin: dict[str, str] = {}
    cases_path = Path(cases_dir)
    for subdir in ["generated", "manual"]:
        sub = cases_path / subdir
        if sub.is_dir():
            for f in sorted(sub.glob("*.json")):
                try:
                    data = json.loads(f.read_text())
                    key = f"position_{len(positions):04d}"
                    positions[key] = data
                    position_origin[key] = f"{subdir}/{f.name}"
                except (json.JSONDecodeError, OSError):
                    continue

    # 如果指定了 market_id，过滤 positions
    if market_id:
        positions = {
            k: v for k, v in positions.items()
            if v.get("market_id", "CN_A_STOCK") == market_id
        }

    # 夹具指纹：把「实际参与评测的是哪些数据」绑进 config_hash。
    # 此前 fixtures_fingerprint 参与哈希却在全仓没有任何赋值点，永远取到 None——
    # 换掉整套夹具哈希都不变，compare_baseline 照样判「可比」，可比性在数据面形同虚设。
    digest = hashlib.sha256()
    for key in sorted(positions):
        digest.update(key.encode())
        digest.update(b"\0")
        digest.update(position_origin.get(key, "?").encode())
        digest.update(b"\0")
        digest.update(
            json.dumps(positions[key], sort_keys=True, default=str).encode()
        )
        digest.update(b"\0")
    config["fixtures_fingerprint"] = digest.hexdigest()[:16]
    config["fixture_count"] = len(positions)


    # 绑定：把案例分散到**实际载入的夹具**上。
    # 生成器只写 position_{i:04d}，而 positions 的编号来自「按文件名排序后依次编号」，
    # 于是四类案例全部挤在排序最靠前的 cases_per_type 个夹具上：其余夹具一条都进不了
    # 引擎，而且「哪几个夹具参与」取决于文件名首字母，与案例想考的约束毫无关系。
    # 只覆盖机器分配的 ref（^position_\d{4}$），调用方显式绑定的原样保留。
    all_cases = bind_machine_refs(all_cases, positions)
    # 「实际绑定到多少个夹具」必须报出来：fixture_count 只是载入总数，两者不等时
    # 覆盖类说法只对已绑定的部分成立。
    config["fixtures_bound"] = len({c.position_ref for c in all_cases})

    # 案例引用的夹具必须存在，否则引擎会静默退回「第一个夹具」甚至硬编码假持仓。
    # 先在这里拦住：宁可结构化报错，也不要产出一堆「跑在别的数据上」的评测结论。
    unbound = sorted({
        c.position_ref for c in all_cases
        if positions and c.position_ref not in positions
    })
    if unbound:
        raise InputValidationError(
            "position_ref_unbound",
            f"{len(unbound)} 个案例引用了不存在的夹具：{unbound[:5]}",
            hint=f"cases_dir={cases_dir} 实际载入 {len(positions)} 个夹具；"
                 "请检查 injection_types 配比与夹具数量是否匹配",
        )
    # model_id 未显式配置时按被测系统推断，避免「换了 trader 但哈希不变」
    if not config.get("model_id_explicit"):
        default_model = "stub-trader" if trader_name == "stub" else f"{trader_name}-trader"
        if config.get("model_id") in (None, "", "stub-trader"):
            config["model_id"] = default_model

    # 运行评测
    report = run_evaluation(
        cases=all_cases,
        positions=positions,
        trader_fn=trader_fn,
        config=config,
        output_dir=output_dir,
    )

    # agent 模式：记录作答完整度并写入报告
    if trader_name == "agent":
        stats = getattr(trader_fn, "__agent_stats__", {}) or {}
        answered = stats.get("answered") or 0
        unanswered = stats.get("unanswered") or 0
        total_req = stats.get("total_requests") or (answered + unanswered)
        report["agent_stats"] = {
            **stats,
            "total_requests": total_req,
            "answer_rate": round(answered / total_req, 4) if total_req else 0.0,
            "note": "未作答按 hold 处理；answer_rate 过低会使违反率失真",
        }
        report["value_assessment"]["trader_meta"] = {
            "kind": "agent",
            "model_id": config.get("model_id", "agent-trader"),
            "rules_in_prompt": config.get("rules_in_prompt"),
            "stats": report["agent_stats"],
        }
        if total_req and answered / total_req < 0.8:
            report["value_assessment"]["verdict"] = {
                "verdict": "inconclusive",
                "reason": "low_answer_rate",
                "significant": False,
                "p_value": report.get("mcnemar_test", {}).get("p_value", 1.0),
                "actionable": False,
                "note": (
                    f"作答率仅 {answered}/{total_req}，未作答部分按 hold 处理，"
                    "违反率会被系统性低估；请补全决策后重跑。"
                ),
            }
        if output_dir:
            with open(Path(output_dir) / "report.json", "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="AI Trader Constraint Stress Test"
    )
    parser.add_argument("--cases-dir", help="Fixture directory path")
    parser.add_argument("--config", help="Config file path")
    parser.add_argument("--output-dir", help="Output directory")
    parser.add_argument("--market-id", help="Filter by market ID")
    parser.add_argument(
        "--injection-types",
        nargs="+",
        choices=["duplicate", "prompt_perturb", "missing_quote", "timeout_retry"],
        help="Filter injection types",
    )
    parser.add_argument("--self-check", action="store_true", help="Run env self-check")
    parser.add_argument("--json", action="store_true", default=True)

    try:
        args = parser.parse_args()
    except SystemExit as e:
        return _emit(
            {
                "ok": False,
                "error": {
                    "code": "missing_argument",
                    "message": str(e),
                    "hint": "Run with --help for usage",
                },
            },
            2,
        )

    if args.self_check:
        from scripts.env_check import run_self_check

        report = run_self_check()
        return _emit({"ok": report["ok"], "self_check": report}, 0 if report["ok"] else 1)

    try:
        result = run(
            cases_dir=args.cases_dir,
            config_path=args.config,
            output_dir=args.output_dir,
            market_id=args.market_id,
            injection_types=args.injection_types,
        )
        return _emit({"ok": True, "result": result}, 0)

    except InputValidationError as e:
        return _emit(
            {
                "ok": False,
                "error": {
                    "code": e.code,
                    "message": e.message,
                    "hint": e.hint,
                },
            },
            2,
        )
    except KeyboardInterrupt:
        return _emit(
            {
                "ok": False,
                "error": {
                    "code": "interrupted",
                    "message": "User interrupted",
                    "hint": "",
                },
            },
            130,
        )
    except Exception as e:
        return _emit(
            {
                "ok": False,
                "error": {
                    "code": "runtime_error",
                    "message": str(e),
                    "hint": "Check logs for details",
                },
            },
            1,
        )


if __name__ == "__main__":
    sys.exit(main())
