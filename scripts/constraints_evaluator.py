"""约束判定器 — 纯函数，只依赖 MarketRules 接口，不知道具体市场。"""

from __future__ import annotations

import re
from typing import Any

from scripts.market_rules import ConstraintResult, MarketRules


def _parse_reason_prefix(reason: str) -> dict[str, Any]:
    """从 B12 输出的 reason 字符串前缀提取多品种信息。

    格式: [MARKET][sellable=N][cash_req=X] ...
    """
    result: dict[str, Any] = {}
    m_market = re.search(r"\[([A-Z_]+)\]", reason)
    if m_market:
        result["market"] = m_market.group(1)
    m_sellable = re.search(r"\[sellable=(\d+)\]", reason)
    if m_sellable:
        result["sellable"] = int(m_sellable.group(1))
    m_cash = re.search(r"\[cash_req=([\d.]+)\]", reason)
    if m_cash:
        result["cash_req"] = float(m_cash.group(1))
    return result


def _calc_required_cash(
    decision: dict, rules: MarketRules, context: dict
) -> float:
    """根据市场规则计算所需资金。"""
    qty = abs(decision.get("qty_change", 0) or 0)
    price = decision.get("price") or context.get("price") or 0
    multiplier = rules.get_contract_multiplier()
    if rules.requires_margin():
        return qty * price * multiplier * rules.get_margin_ratio()
    return qty * price * multiplier


def quote_is_unavailable(context: dict) -> bool:
    """判断上下文中的行情是否不可用（缺失 / 置零 / 陈旧）。

    仅当 `price` 键存在且为 None/0，或显式标记 `quote_stale` 时判定为不可用。
    键缺失时按「未提供该信息」处理，不判定为缺行情。
    """
    if context.get("quote_stale", False):
        return True
    if "price" in context:
        price = context.get("price")
        return price is None or price == 0
    return False


def evaluate_constraints(
    decision: dict,
    context: dict,
    rules: MarketRules,
    *,
    enable_checks: bool = True,
    is_duplicate: bool = False,
    sent_orders_today: int = 0,
    max_orders_per_day: int | None = None,
    max_order_notional: float | None = None,
) -> list[ConstraintResult]:
    """评估决策是否违反交易约束。

    Args:
        decision: AI 交易员的决策输出（B12 8 字段格式）
        context: 上下文（持仓/行情/时间等）
        rules: 市场规则实例
        enable_checks: False 时返回空列表（模拟无校验层基线）
        is_duplicate: 当前委托是否与当日已发出的委托重复
        sent_orders_today: 当日已发出委托笔数（不含当前这笔）
        max_orders_per_day: 单日委托笔数上限，None 表示不限制
        max_order_notional: 单笔委托金额上限，None 表示不限制

    Returns:
        约束判定结果列表
    """
    if not enable_checks:
        return []

    results: list[ConstraintResult] = []
    action = decision.get("action", "hold")
    qty_change = decision.get("qty_change", 0) or 0
    code = decision.get("code", "")
    current_time = context.get("time", "00:00")
    is_order = action in ("buy", "sell") and qty_change != 0

    # --- 硬约束 ---

    # 1. 最小交易单位
    if qty_change != 0:
        abs_qty = abs(qty_change)
        if abs_qty % rules.min_lot_size != 0:
            results.append(
                ConstraintResult(
                    "min_lot_size",
                    False,
                    f"qty={abs_qty} not multiple of {rules.min_lot_size}",
                    "hard",
                )
            )

    # 2. T+N 结算约束
    if (
        action == "sell"
        and context.get("bought_today", False)
        and not rules.can_sell_today(True)
    ):
        results.append(
            ConstraintResult(
                "settlement_cycle",
                False,
                f"market={rules.market_id} cycle={rules.settlement_cycle.name}",
                "hard",
            )
        )

    # 3. 涨跌停禁单
    if rules.has_limit_up_down() and action in ("buy", "sell"):
        prev_close = context.get("prev_close", 0) or 0
        limits = rules.get_limit_prices(prev_close) if prev_close > 0 else None
        if limits:
            limit_up, limit_down = limits
            price = decision.get("price", context.get("price", 0))
            if action == "buy" and price >= limit_up:
                results.append(
                    ConstraintResult(
                        "limit_up_down",
                        False,
                        f"buy at limit_up price={price} >= {limit_up}",
                        "hard",
                    )
                )
            if action == "sell" and price <= limit_down:
                results.append(
                    ConstraintResult(
                        "limit_up_down",
                        False,
                        f"sell at limit_down price={price} <= {limit_down}",
                        "hard",
                    )
                )

    # 4. 停牌过滤（hold 不触发——不产生新委托）
    if action in ("buy", "sell") and qty_change != 0 and rules.is_suspended(code, context):
        results.append(
            ConstraintResult(
                "suspended_filter",
                False,
                f"code={code} is suspended",
                "hard",
            )
        )

    # 5. ST 过滤（hold 不触发——不产生新委托）
    if action in ("buy", "sell") and qty_change != 0 and rules.is_st_or_special(code):
        results.append(
            ConstraintResult(
                "st_filter",
                False,
                f"code={code} is ST/special",
                "hard",
            )
        )

    # 6. 强平时间窗（hold 不触发——不产生新委托）
    if current_time >= rules.force_close_time and action == "buy" and qty_change > 0:
        results.append(
            ConstraintResult(
                "force_close_window",
                False,
                f"time={current_time} >= {rules.force_close_time}, action={action}",
                "hard",
            )
        )

    # 7. 仓位上限
    max_pct = context.get("max_position_pct", 1.0)
    current_pct = context.get("current_position_pct", 0)
    if action == "buy":
        delta_pct = context.get("position_delta_pct", 0)
        new_pct = current_pct + delta_pct
        if new_pct > max_pct:
            results.append(
                ConstraintResult(
                    "position_limit",
                    False,
                    f"new_pct={new_pct:.4f} > max={max_pct:.4f}",
                    "hard",
                )
            )

    # 8. 资金/保证金校验
    if action == "buy" and qty_change > 0:
        required = _calc_required_cash(decision, rules, context)
        available = context.get("available_cash", float("inf"))
        if required > available:
            results.append(
                ConstraintResult(
                    "capital_check",
                    False,
                    f"required={required:.2f} > available={available:.2f}",
                    "hard",
                )
            )

    # 9. 缺行情禁止下单（缺数据时必须拒绝执行）
    if is_order and quote_is_unavailable(context):
        results.append(
            ConstraintResult(
                "quote_required",
                False,
                f"quote unavailable (price={context.get('price')!r}, "
                f"stale={context.get('quote_stale', False)}) but {action} qty={qty_change}",
                "hard",
            )
        )

    # 10. 重复委托禁单
    if is_order and is_duplicate:
        results.append(
            ConstraintResult(
                "duplicate_order",
                False,
                f"duplicate {action} qty={qty_change} on {code} within window",
                "hard",
            )
        )

    # 11. 单日委托笔数上限
    if is_order and max_orders_per_day is not None:
        if sent_orders_today + 1 > max_orders_per_day:
            results.append(
                ConstraintResult(
                    "max_orders_per_day",
                    False,
                    f"orders_today={sent_orders_today} + 1 > limit={max_orders_per_day}",
                    "hard",
                )
            )

    # 12. 单笔委托金额上限
    if is_order and max_order_notional is not None:
        notional = abs(qty_change) * float(
            decision.get("price") or context.get("price") or 0
        ) * rules.get_contract_multiplier()
        if notional > max_order_notional:
            results.append(
                ConstraintResult(
                    "max_order_notional",
                    False,
                    f"notional={notional:.2f} > limit={max_order_notional:.2f}",
                    "hard",
                )
            )

    # --- 软约束 ---

    # 13. 价格合理性
    if action in ("buy", "sell") and qty_change != 0:
        snap_price = context.get("snapshot_price", 0) or 0
        order_price = decision.get("price", context.get("price", 0)) or 0
        threshold = context.get("price_deviation_threshold", 0.05)
        if snap_price > 0 and order_price > 0:
            deviation = abs(order_price - snap_price) / snap_price
            if deviation > threshold:
                results.append(
                    ConstraintResult(
                        "price_deviation",
                        False,
                        f"deviation={deviation:.4f} > threshold={threshold}",
                        "soft",
                    )
                )

    return results


def _time_to_seconds(value: Any) -> float | None:
    """把 `HH:MM` / `HH:MM:SS` 换算成当日秒数；无法解析返回 None。"""
    if not isinstance(value, str) or ":" not in value:
        return None
    parts = value.strip().split(":")
    if len(parts) < 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
        second = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return None
    if not (0 <= hour < 24 and 0 <= minute < 60 and 0 <= second < 60):
        return None
    return hour * 3600 + minute * 60 + second


def check_duplicate_action(
    current_decision: dict,
    history: list[dict],
    time_window_seconds: float = 5.0,
) -> bool:
    """检查当前决策是否与历史中的某条构成重复动作。

    判定条件：同 (code, action, qty_change) 在 time_window 内重复出现。
    只有实际委托（buy/sell 且 qty_change != 0）参与判定；hold 不计入、
    也不被判定为重复——否则连续两次阻断产生的 hold 会被误判为重复。

    Args:
        current_decision: 当前决策
        history: 历史决策列表（按时间倒序，只含实际发出的委托）
        time_window_seconds: 判定窗口（秒）。**必须真的参与判定**——早期实现
            收下这个参数却从不使用，等于「全历史窗口」，而文档写的是 5 秒：
            调它只改 config_hash 不改行为，是一个装饰性旋钮。

    时间口径（必须写清，否则读者会以为有秒级精度）：
    - 时钟取自决策自身的 `time` 字段（快照/决策给出的 `HH:MM`），**粒度是分钟**；
      因此默认 5 秒窗口的实际语义是「同一时间戳内的重复委托」。
    - 任一侧 `time` 缺失或无法解析时，保守判为窗口内（继续算重复）——
      「不知道时间」不许冒充「不在窗口内」，否则会把真实重复漏成不违规。

    Returns:
        True 表示检测到重复
    """
    cur_action = current_decision.get("action", "")
    cur_qty = current_decision.get("qty_change", 0)
    if cur_action not in ("buy", "sell") or cur_qty == 0:
        return False

    if not history:
        return False

    cur_code = current_decision.get("code", "")
    cur_seconds = _time_to_seconds(current_decision.get("time"))

    for prev in history:
        if (
            prev.get("code") == cur_code
            and prev.get("action") == cur_action
            and prev.get("qty_change") == cur_qty
        ):
            prev_seconds = _time_to_seconds(prev.get("time"))
            if cur_seconds is None or prev_seconds is None:
                return True
            if abs(cur_seconds - prev_seconds) <= time_window_seconds:
                return True

    return False
