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


class SutUnavailable(RuntimeError):
    """被测系统不可用：缺目录、缺 run()、或解析到了本包自己。

    这是**必须先于回测失败**的条件。此前 `from build import run` 在本包
    `scripts/build.py` 也在 `sys.path` 上时，会静默导入本包自己的 `run()`
    （返回 dict 而非 list），随后被 `except Exception` 吞成逐条 hold ——
    结果是退出码 0、`config_hash` 不变、报告看起来正常，但 `baseline_order_events=0`、
    违规 0、拦截 0，`inconclusive`。那是最坏的一种失败：读起来像「被测系统很守规」，
    实际是「被测系统根本没跑」。现在改成结构化报错，宁可不出报告。
    """

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


def resolve_sut() -> Any:
    """Import the SUT's ``run`` after proving it comes from the SUT tree.

    模块**来源**必须落在 :data:`SUT_SCRIPTS` 下。仅检查 `import` 成功不够：
    本包的 `scripts/build.py` 同样暴露 `run()`，名字冲突会让「没装 SUT」
    看起来像「SUT 可用」。
    """
    if not SUT_SCRIPTS.is_dir():
        raise SutUnavailable(
            "被测系统脚本目录不存在: %s" % SUT_SCRIPTS,
            hint="设置 CONSTRAINT_SUT_SCRIPTS 指向被测系统的 scripts/，"
                 "或把 skill-intraday-position-manager 放到同级目录",
        )
    resolved = SUT_SCRIPTS.resolve()
    if str(resolved) not in sys.path:
        sys.path.insert(0, str(resolved))
    try:
        from build import run as sut_run  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SutUnavailable(
            "无法从 %s 导入 run(): %s" % (resolved, exc),
            hint="被测系统需提供 scripts/build.py 与 run(input, config)",
        ) from exc

    origin = Path(getattr(sut_run, "__module__", "") and
                  getattr(sys.modules.get(sut_run.__module__), "__file__", "") or "")
    try:
        origin_resolved = Path(str(sut_run.__globals__.get("__file__", ""))).resolve()
    except (TypeError, ValueError):
        origin_resolved = Path(str(origin)).resolve() if str(origin) else None
    if origin_resolved is None or resolved not in origin_resolved.parents:
        raise SutUnavailable(
            "run() 解析到了 %s，不在被测系统目录 %s 下——很可能是本包自己的 "
            "scripts/build.py 顶替了被测系统" % (origin_resolved or "<unknown>", resolved),
            hint="确认被测系统目录里有 build.py，且 CONSTRAINT_SUT_SCRIPTS 指对了",
        )
    return sut_run


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
        sut_run = resolve_sut()
        results = sut_run(sut_input)
        if results and len(results) > 0:
            decision = results[0]
            # 被测系统输出的 current_qty 需要补上 price 供约束判定器使用
            decision["price"] = float(price)
            return decision
        return _make_hold(code, pnl_pct, total_qty, time_str, "[EMPTY] SUT returned empty")

    except SutUnavailable:
        # 依赖缺失是**配置错误**，不是「被测系统拒绝执行」：绝不能用 hold 冒充，
        # 否则整份报告会退化成「零违规、零拦截」的假象。
        raise
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
