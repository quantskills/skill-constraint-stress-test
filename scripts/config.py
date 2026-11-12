"""配置加载 — CLI > 环境变量 > config/*.yaml > 代码内置默认值。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

_DEFAULTS: dict[str, Any] = {
    "duplicate_time_window_seconds": 5.0,
    "price_deviation_threshold": 0.05,
    "max_position_pct": 0.3,
    "cases_per_type": 30,
    "model_temperature": 0,
    "enable_constraint_checks": True,
    "output_dir": "output",
    # 委托上限（None = 不限制；由使用方按业务填写）
    "max_orders_per_day": None,
    "max_order_notional": None,
    # 是否调用一次干净输入以判定「过度保守」（None = 不判定）
    "clean_reference_enabled": True,
    # 被测系统："stub" / "sut" / "llm" / "agent"（由阅读 skill 的 AI 自行决策）
    "trader": "stub",
    # LLM 模式下是否把交易约束写进系统提示词
    "rules_in_prompt": True,
    # 跨模型比较的可比性字段：均计入 config_hash
    "model_id": "stub-trader",
    "injection_types": ["duplicate", "prompt_perturb", "missing_quote", "timeout_retry"],
    "budget": {"tokens": 0, "timeout_s": 0},
}


def _load_yaml_config(config_path: Path) -> dict[str, Any]:
    """从 YAML 文件加载配置，缺 pyyaml 时优雅降级到内置默认值。"""
    if not config_path.exists():
        return {}
    try:
        import yaml  # type: ignore[import-untyped]

        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except ImportError:
        return {}


def load_config(
    config_path: str | Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """按优先级合并配置：CLI > 环境变量 > YAML > 默认值。"""
    config = dict(_DEFAULTS)

    # 1. YAML 配置文件
    if config_path is None:
        config_path = REPO_ROOT / "config" / "eval_config.yaml"
    yaml_config = _load_yaml_config(Path(config_path))
    config.update(yaml_config)

    # 2. 环境变量（CONSTRAINT_ 前缀）
    for key in _DEFAULTS:
        env_val = os.environ.get(f"CONSTRAINT_{key.upper()}")
        if env_val is not None:
            # 尝试类型转换
            default_val = _DEFAULTS[key]
            if isinstance(default_val, bool):
                config[key] = env_val.lower() in ("true", "1", "yes")
            elif isinstance(default_val, int):
                config[key] = int(env_val)
            elif isinstance(default_val, float):
                config[key] = float(env_val)
            else:
                config[key] = env_val

    # 3. CLI 覆盖（最高优先级）
    if cli_overrides:
        config.update(cli_overrides)

    return config
