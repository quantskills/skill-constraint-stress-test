"""主入口 CLI — 约束压力测试评测编排器。

CLI 错误契约：stdout 只输出一个纯 JSON 对象，退出码区分 0/1/2/130。
"""

from __future__ import annotations

import json
import sys
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
        from scripts.llm_trader import get_stats, llm_trader_from_env, reset_stats
        reset_stats()
        trader_fn = llm_trader_from_env()
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
    active_types = injection_types or [
        "duplicate",
        "prompt_perturb",
        "missing_quote",
        "timeout_retry",
    ]

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
    cases_path = Path(cases_dir)
    for subdir in ["generated", "manual"]:
        sub = cases_path / subdir
        if sub.is_dir():
            for f in sorted(sub.glob("*.json")):
                try:
                    data = json.loads(f.read_text())
                    positions[f"position_{len(positions):04d}"] = data
                except (json.JSONDecodeError, OSError):
                    continue

    # 如果指定了 market_id，过滤 positions
    if market_id:
        positions = {
            k: v for k, v in positions.items()
            if v.get("market_id", "CN_A_STOCK") == market_id
        }


    # llm 模式下，模型标识与温度以环境变量为准，保证 config_hash 反映真实配置
    if trader_name == "llm":
        import os as _os
        if _os.environ.get("CONSTRAINT_LLM_MODEL"):
            config["model_id"] = _os.environ["CONSTRAINT_LLM_MODEL"]
            config["model_id_explicit"] = True
        if not config.get("model_id_explicit"):
            config["model_id"] = "llm-trader"
        if "model_temperature" not in config or config.get("model_temperature") == 0:
            try:
                config["model_temperature"] = float(
                    _os.environ.get("CONSTRAINT_LLM_TEMPERATURE", "0")
                )
            except ValueError:
                pass
        # rules_in_prompt 以环境变量为准，否则报告会与实际 prompt 不符
        if "CONSTRAINT_LLM_RULES_IN_PROMPT" in _os.environ:
            config["rules_in_prompt"] = _os.environ[
                "CONSTRAINT_LLM_RULES_IN_PROMPT"
            ].lower() in ("1", "true", "yes")

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

    # LLM 模式下把调用统计并入报告；解析失败率过高时降级 verdict
    if trader_name == "llm":
        from scripts.llm_trader import get_stats
        stats = get_stats()
        report["llm_stats"] = stats
        report["value_assessment"]["trader_meta"] = {
            "kind": "llm",
            "model_id": config.get("model_id"),
            "temperature": config.get("model_temperature"),
            "rules_in_prompt": config.get("rules_in_prompt", True),
            "stats": stats,
        }
        # 解析失败率 > 20% 时，决策并非模型真实意图，结论不可信
        if stats.get("parse_error_rate", 0) > 0.2:
            report["value_assessment"]["verdict"] = {
                "verdict": "inconclusive",
                "reason": "high_parse_error_rate",
                "significant": False,
                "p_value": report.get("mcnemar_test", {}).get("p_value", 1.0),
                "actionable": False,
                "note": (
                    f"模型输出解析失败率 {stats['parse_error_rate']:.2%} 过高，"
                    "决策无法代表模型真实意图；请检查提示词与输出格式，"
                    "或提高 max_tokens 后重跑。"
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
