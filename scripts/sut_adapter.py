"""日内仓位管理适配器 — 将其决策模块接入本回放引擎。

被测系统的 run() 是数据驱动型：输入 dict，输出 list[dict]。
本引擎的 trader_fn 签名：(perturbed_input, config) -> dict。

本模块做两件事：
1. 格式适配：本引擎的 perturbed_input → 被测系统 7 字段输入
2. 异常捕获：被测系统校验失败时返回保守 hold（模拟「拒绝执行」）

被测系统目录通过环境变量 `CONSTRAINT_SUT_SCRIPTS` 指定，默认指向同级的
`skill-intraday-position-manager/scripts`。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# 将被测系统 scripts/ 加入 sys.path

_SKILL_ROOT = Path(__file__).resolve().parents[1]
SUT_SCRIPTS = Path(
    os.environ.get(
        "CONSTRAINT_SUT_SCRIPTS",
        _SKILL_ROOT.parent / "skill-intraday-position-manager" / "scripts",
    )
)
_SUT_RESOLVED = SUT_SCRIPTS.resolve()
if _SUT_RESOLVED.is_dir() and str(_SUT_RESOLVED) not in sys.path:
    sys.path.insert(0, str(_SUT_RESOLVED))


def _make_hold(code: str, pnl_pct: float, qty: int, time_str: str, reason: str) -> dict:
    return {
        "code": code,
        "pnl_pct": pnl_pct,
        "current_qty": qty,
        "time": time_str,
        "action": "hold",
        "qty_change": 0,
        "target_qty": qty,
        "reason": reason,
    }


def sut_trader(perturbed_input: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """把日内仓位管理 skill 适配为本引擎的被测系统。

    处理边界情况：
    - price 为 None/0 时被测系统校验会失败 → 返回 hold（拒绝执行）
    - 其他校验失败 → 返回 hold + 错误原因
    """
    code = perturbed_input.get("code", "UNKNOWN")
    pnl_pct = perturbed_input.get("pnl_pct", 0)
    sellable = perturbed_input.get("sellable_qty", 0)
    locked = perturbed_input.get("locked_qty", 0)
    price = perturbed_input.get("price")
    cash = perturbed_input.get("available_cash", 0)
    time_str = perturbed_input.get("time", "10:30")
    total_qty = sellable + locked

    # 价格无效 → 拒绝执行（这是正确的保守行为）
    if price is None or price == 0 or not isinstance(price, (int, float)):
        return _make_hold(
            code, pnl_pct, total_qty, time_str,
            "[REFUSAL] price invalid or missing — conservative hold",
        )

    # 构造被测系统输入
    sut_input = {
        "code": code,
        "pnl_pct": float(pnl_pct),
        "sellable_qty": int(sellable),
        "locked_qty": int(locked),
        "price": float(price),
        "available_cash": float(cash),
        "time": time_str,
    }

    try:
        from build import run as sut_run

        results = sut_run(sut_input)
        if results and len(results) > 0:
            decision = results[0]
            # 被测系统输出的 current_qty 需要补上 price 供约束判定器使用
            decision["price"] = float(price)
            return decision
        return _make_hold(code, pnl_pct, total_qty, time_str, "[EMPTY] SUT returned empty")

    except (ValueError, TypeError, KeyError) as e:
        return _make_hold(
            code, pnl_pct, total_qty, time_str,
            f"[REFUSAL] SUT validation failed: {e}",
        )
    except Exception as e:
        return _make_hold(
            code, pnl_pct, total_qty, time_str,
            f"[ERROR] SUT unexpected: {e}",
        )
