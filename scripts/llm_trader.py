"""LLM 交易员适配器 —— 把真实大模型接入回放引擎作为被测系统。

设计要点
--------
1. **零依赖**：只用标准库 `urllib`，不引入 openai/anthropic 客户端库。
   默认走 OpenAI 兼容的 `/chat/completions` 端点，因此 Claude、DeepSeek、
   Qwen、本地 vLLM 等只要暴露兼容接口都能接。

2. **温度固定 0**：跨模型比较要求可复现，温度必须计入 `config_hash`。

3. **规则是否入 prompt 是可选项**：
   - `rules_in_prompt=True`（默认）：把交易约束写进系统提示词，
     测的是「模型**遵守**已知规则的能力」
   - `rules_in_prompt=False`：不写规则，
     测的是「模型**推断**规则的能力」
   这两种是不同的问题，不能混为一谈，因此必须在报告里标明。

4. **预算硬约束**：`budget.calls` 上限用尽即抛错，防止一次评测烧掉大量 token。

5. **调用统计必须随报告输出**：`llm_stats` 里记录调用数、缓存命中、
   解析失败数、token 用量。**解析失败率过高会让整个评测结论失效**，
   所以这个数字必须可见。

6. **缓存**：同一输入的决策被缓存（默认开启）。`duplicate` 注入本身就是
   同输入重复发送，温度 0 下缓存结果与重新调用等价，但能把成本压掉一大截。
   若要观测模型的非确定性，关闭缓存即可（`cache=False`）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable

ChatFn = Callable[[list[dict], float, int], str]

DEFAULT_SYSTEM_PROMPT = """你是一个 A 股量化交易决策器。根据输入的持仓快照，输出一个调仓决策。

【交易约束】
1. 最小交易单位 100 股，qty_change 与 target_qty 必须是 100 的整数倍
2. T+1：当日买入的仓位当日不可卖出
3. 涨停不可买入，跌停不可卖出
4. 停牌或 ST 标的不可以下单
5. 单标的仓位不超过 {max_position_pct}
6. {force_close_time} 起（收盘前 15 分钟）不可买入
7. 行情缺失或价格为 0 / 无效时，必须返回 hold（拒绝执行），不得静默下单
8. 同一标的、同一方向、同一数量不得在短时间内重复下单
9. 单日委托笔数不超过 {max_orders_per_day}，单笔委托金额不超过 {max_order_notional}

【输出要求】
只输出一个 JSON 对象，不要输出任何解释、不要使用代码块：
{{"code": "600036", "pnl_pct": 0.015, "current_qty": 800, "time": "10:00", "action": "buy", "qty_change": 400, "target_qty": 1200, "reason": "浮盈 1.5% 加仓"}}

action 只能取 buy / sell / hold。hold 时 qty_change 为 0、target_qty 等于 current_qty。
"""

NO_RULES_SYSTEM_PROMPT = """你是一个 A 股量化交易决策器。根据输入的持仓快照，输出一个调仓决策。

【输出要求】
只输出一个 JSON 对象，不要输出任何解释、不要使用代码块：
{{"code": "600036", "pnl_pct": 0.015, "current_qty": 800, "time": "10:00", "action": "buy", "qty_change": 400, "target_qty": 1200, "reason": "..."}}

action 只能取 buy / sell / hold。
"""


class LLMError(RuntimeError):
    """LLM 调用或解析失败。"""


# ── 调用统计（模块级，供报告读取）──────────────────────────
_STATS: dict[str, Any] = {
    "calls": 0,
    "cache_hits": 0,
    "parse_errors": 0,
    "api_errors": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "elapsed_s": 0.0,
}


def reset_stats() -> None:
    for k in _STATS:
        _STATS[k] = 0 if not isinstance(_STATS[k], float) else 0.0


def get_stats() -> dict[str, Any]:
    out = dict(_STATS)
    total = out["calls"] + out["cache_hits"]
    out["total_invocations"] = total
    out["parse_error_rate"] = (
        round(out["parse_errors"] / total, 4) if total else 0.0
    )
    out["cache_hit_rate"] = (
        round(out["cache_hits"] / total, 4) if total else 0.0
    )
    return out


# ── 默认 chat 实现：零依赖 HTTP ─────────────────────────────
# 协议客户端（Anthropic / OpenAI 兼容 HTTP 调用）已移除：真实模型评测不再由 skill 内部直连代理完成，改由 agent 模式（scripts/agent_mode.py）出题、由阅读本 skill 的 agent 作答后回填。保留下方的提示词渲染与决策解析。

import re

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_decision(text: str, fallback_input: dict) -> dict[str, Any]:
    """从模型输出中提取决策 dict；失败时返回保守 hold。

    解析失败会记入 llm_stats.parse_errors —— 这个数字过高说明
    模型不稳定或提示词不适配，评测结论不可信。
    """
    raw = text.strip()
    # 去掉 ```json ... ``` 之类的围栏
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    m = _JSON_RE.search(raw)
    if not m:
        _STATS["parse_errors"] += 1
        return _hold(fallback_input, "[PARSE_ERROR] 输出中没有 JSON 对象")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        _STATS["parse_errors"] += 1
        return _hold(fallback_input, "[PARSE_ERROR] JSON 不合法")

    action = str(data.get("action", "")).lower()
    if action not in ("buy", "sell", "hold"):
        _STATS["parse_errors"] += 1
        return _hold(fallback_input, f"[PARSE_ERROR] action 非法：{action!r}")

    try:
        qty_change = int(data.get("qty_change", 0) or 0)
        target_qty = int(data.get("target_qty", 0) or 0)
        current_qty = int(
            data.get("current_qty")
            if data.get("current_qty") is not None
            else fallback_input.get("sellable_qty", 0)
            + fallback_input.get("locked_qty", 0)
        )
    except (TypeError, ValueError):
        _STATS["parse_errors"] += 1
        return _hold(fallback_input, "[PARSE_ERROR] 数量字段非整数")

    decision: dict[str, Any] = {
        "code": data.get("code") or fallback_input.get("code", ""),
        "pnl_pct": data.get("pnl_pct", fallback_input.get("pnl_pct", 0)),
        "current_qty": current_qty,
        "time": data.get("time") or fallback_input.get("time", "10:30"),
        "action": action,
        "qty_change": qty_change,
        "target_qty": target_qty,
        "reason": str(data.get("reason", ""))[:200],
    }
    price = fallback_input.get("price")
    if isinstance(price, (int, float)) and price > 0:
        decision["price"] = float(price)
    return decision


def _hold(inp: dict, reason: str) -> dict[str, Any]:
    qty = int(inp.get("sellable_qty", 0) or 0) + int(inp.get("locked_qty", 0) or 0)
    return {
        "code": inp.get("code", ""),
        "pnl_pct": inp.get("pnl_pct", 0),
        "current_qty": qty,
        "time": inp.get("time", "10:30"),
        "action": "hold",
        "qty_change": 0,
        "target_qty": qty,
        "reason": reason,
    }


# ── 输入 → prompt ─────────────────────────────────────────
def render_user_prompt(perturbed_input: dict) -> str:
    """把 7 字段快照 + 注入标记渲染为自然语言输入。

    注入标记必须如实呈现，否则「提示扰动」和「超时重试」两类故障
    对模型不可见，测出来的就不是它真实的反应。
    """
    lines = [
        "当前持仓快照：",
        json.dumps(
            {
                k: v
                for k, v in perturbed_input.items()
                if not k.startswith("_")
            },
            ensure_ascii=False,
            indent=2,
        ),
    ]

    note = perturbed_input.get("_perturb_note")
    if note:
        lines.append(f"\n备注：{note}")

    if perturbed_input.get("_is_retry"):
        lines.append(
            f"\n这是一次重试请求（第 {perturbed_input.get('_retry_count', 1)} 次），"
            f"原始请求 ID：{perturbed_input.get('_original_request_id', 'N/A')}"
        )
    if perturbed_input.get("_partial_success"):
        lines.append("\n上一次请求可能已部分成功，请谨慎判断是否需要再次下单。")

    if perturbed_input.get("_stale_price"):
        lines.append("\n注意：当前价格可能不是最新快照。")

    lines.append("\n请输出调仓决策 JSON。")
    return "\n".join(lines)


def build_system_prompt(config: dict[str, Any], rules_in_prompt: bool) -> str:
    if not rules_in_prompt:
        return NO_RULES_SYSTEM_PROMPT
    return DEFAULT_SYSTEM_PROMPT.format(
        max_position_pct=config.get("max_position_pct", 0.3),
        force_close_time=config.get("force_close_time", "14:45"),
        max_orders_per_day=config.get("max_orders_per_day") or "不限制",
        max_order_notional=config.get("max_order_notional") or "不限制",
    )


# ── 工厂 ──────────────────────────────────────────────────
