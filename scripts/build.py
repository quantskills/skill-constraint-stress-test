"""标准 skill 入口 —— 实现 `run(input_data, config=None)` 契约。

本 skill 的实际评测编排在 `scripts/run_eval.py`（同为 CLI 入口）。
此文件把标准的 dict 输入映射为评测参数，供 Agent / Alpha 统一调用。

input_data 支持两种形态：
    - dict：{"cases_dir": ..., "output_dir": ..., "market_id": ..., "injection_types": [...]}
    - str ：fixture 目录路径（等价于 {"cases_dir": <str>}）

config 为可选的评测配置覆盖，合并优先级：run 参数 > config 覆盖 > 配置文件 > 内置默认值。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.run_eval import InputValidationError, run as _run_eval  # noqa: E402

_ALLOWED_KEYS = {
    "cases_dir",
    "config_path",
    "output_dir",
    "market_id",
    "injection_types",
}


def validate_input(input_data: Any) -> Dict[str, Any]:
    """校验并规范化输入。缺字段/类型错误一律抛 InputValidationError。"""
    if isinstance(input_data, str):
        if not input_data.strip():
            raise InputValidationError(
                "empty_input",
                "input_data 不能为空字符串",
                hint="传入 fixture 目录路径或含 cases_dir 的 dict",
            )
        params: Dict[str, Any] = {"cases_dir": input_data}
    elif isinstance(input_data, dict):
        unknown = set(input_data) - _ALLOWED_KEYS
        if unknown:
            raise InputValidationError(
                "unknown_field",
                f"未知字段：{sorted(unknown)}",
                hint=f"允许字段：{sorted(_ALLOWED_KEYS)}",
            )
        params = dict(input_data)
    else:
        raise InputValidationError(
            "invalid_type",
            f"input_data 必须是 dict 或 str，收到 {type(input_data).__name__}",
        )

    cases_dir = params.get("cases_dir")
    if cases_dir is not None:
        if not isinstance(cases_dir, str):
            raise InputValidationError("invalid_type", "cases_dir 必须是 str")
        if not Path(cases_dir).exists():
            raise InputValidationError(
                "cases_dir_not_found",
                f"cases 目录不存在：{cases_dir}",
                hint="先运行 scripts/fixture_generator.py 生成夹具",
            )

    injection_types = params.get("injection_types")
    if injection_types is not None:
        if not isinstance(injection_types, list) or not all(
            isinstance(t, str) for t in injection_types
        ):
            raise InputValidationError(
                "invalid_type", "injection_types 必须是 list[str]"
            )

    return params


def run(input_data: Any, config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """执行约束压力测试评测，返回聚合统计报告 dict。

    Args:
        input_data: dict 或 fixture 目录路径，见模块 docstring
        config: 可选的评测配置覆盖项

    Returns:
        聚合统计报告 dict（含 run_id / total_events / summary / effect_size）

    Raises:
        InputValidationError: 输入校验失败
    """
    params = validate_input(input_data)
    if config is not None and not isinstance(config, dict):
        raise InputValidationError("invalid_type", "config 必须是 dict 或 None")

    # config 覆盖项透传为 CLI overrides；未识别的键原样交给评测引擎
    merged: Dict[str, Any] = dict(config or {})
    for key in ("output_dir",):
        if key in params and params[key] is not None:
            merged[key] = params[key]

    return _run_eval(
        cases_dir=params.get("cases_dir"),
        config_path=params.get("config_path"),
        output_dir=merged.get("output_dir"),
        market_id=params.get("market_id"),
        injection_types=params.get("injection_types"),
    )


if __name__ == "__main__":
    import json

    try:
        report = run({})
    except InputValidationError as e:
        print(json.dumps(
            {"ok": False, "error": {"code": e.code, "message": e.message, "hint": e.hint}},
            ensure_ascii=False,
        ))
        sys.exit(2)
    except Exception as e:  # 含 LLMError：配置缺失 / 网络失败 / 预算耗尽
        print(json.dumps(
            {
                "ok": False,
                "error": {
                    "code": type(e).__name__,
                    "message": str(e)[:500],
                    "hint": "检查 trader 配置、LLM 环境变量或网络连通性",
                },
            },
            ensure_ascii=False,
        ))
        sys.exit(1)
    print(json.dumps(report, ensure_ascii=False, indent=2))