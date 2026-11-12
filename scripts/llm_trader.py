"""LLM 交易员适配器 —— 把真实大模型接入回放引擎作为被测系统。

设计要点
--------
1. **零依赖**：只用标准库 `urllib`，不引入 openai/anthropic SDK。
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
def _post_json(url: str, payload: dict, headers: dict, timeout_s: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise LLMError(f"HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise LLMError(f"网络错误：{e}") from e


def _split_anthropic_messages(
    messages: list[dict],
) -> tuple[str, list[dict]]:
    """把 system 提示词从 messages 中提出来（Anthropic 用顶层字段）。"""
    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    convo = [m for m in messages if m.get("role") != "system"]
    return "\n\n".join(system_parts), convo


def _extract_anthropic_text(body: dict) -> str:
    """只取 text 块；thinking 块是推理过程，不能当作答案。"""
    blocks = body.get("content", []) or []
    texts = [
        b.get("text", "")
        for b in blocks
        if isinstance(b, dict) and b.get("type") == "text"
    ]
    if not texts:
        raise LLMError(f"响应中没有 text 块：{str(body)[:300]}")
    return "\n".join(texts)


def anthropic_messages_chat(
    messages: list[dict],
    temperature: float = 0.0,
    max_tokens: int = 512,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    timeout_s: float = 60.0,
) -> str:
    """调用 Anthropic 协议的 `/v1/messages`，返回助手文本。

    用于接入本机 LLM 代理（如 127.0.0.1:<port>）这类只暴露
    Anthropic 协议、不提供 OpenAI `/chat/completions` 的端点。

    Anthropic 的 system 提示词是顶层字段，不在 messages 里，
    这里会自动把 messages 中 role=system 的内容提出来。
    """
    base_url = base_url or os.environ.get("CONSTRAINT_LLM_BASE_URL", "")
    api_key = api_key or os.environ.get(
        "CONSTRAINT_LLM_API_KEY"
    ) or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    model = model or os.environ.get("CONSTRAINT_LLM_MODEL", "")

    if not base_url or not model:
        raise LLMError(
            "缺少 LLM 配置：请设置 CONSTRAINT_LLM_BASE_URL 与 CONSTRAINT_LLM_MODEL"
        )

    system_text, convo = _split_anthropic_messages(messages)

    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": convo,
    }
    if system_text:
        payload["system"] = system_text

    body = _post_json(
        base_url.rstrip("/") + "/v1/messages",
        payload,
        {
            "x-api-key": api_key,
            "anthropic-version": os.environ.get(
                "CONSTRAINT_LLM_ANTHROPIC_VERSION", "2023-06-01"
            ),
        },
        timeout_s,
    )

    usage = body.get("usage", {}) or {}
    _STATS["prompt_tokens"] += int(usage.get("input_tokens", 0) or 0)
    _STATS["completion_tokens"] += int(usage.get("output_tokens", 0) or 0)

    return _extract_anthropic_text(body)


def openai_compatible_chat(
    messages: list[dict],
    temperature: float = 0.0,
    max_tokens: int = 512,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    timeout_s: float = 30.0,
) -> str:
    """调用 OpenAI 兼容的 `/chat/completions` 端点。"""
    base_url = base_url or os.environ.get("CONSTRAINT_LLM_BASE_URL", "")
    api_key = api_key or os.environ.get("CONSTRAINT_LLM_API_KEY", "")
    model = model or os.environ.get("CONSTRAINT_LLM_MODEL", "")

    if not base_url or not model:
        raise LLMError(
            "缺少 LLM 配置：请设置 CONSTRAINT_LLM_BASE_URL 与 CONSTRAINT_LLM_MODEL"
        )

    body = _post_json(
        base_url.rstrip("/") + "/chat/completions",
        {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        {"Authorization": f"Bearer {api_key}"} if api_key else {},
        timeout_s,
    )

    usage = body.get("usage", {}) or {}
    _STATS["prompt_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
    _STATS["completion_tokens"] += int(usage.get("completion_tokens", 0) or 0)

    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"响应格式异常：{str(body)[:300]}") from e


def detect_chat_fn() -> ChatFn:
    """按 `CONSTRAINT_LLM_PROTOCOL` 选择协议。

    - `anthropic`：走 `/v1/messages`（本机代理常见）
    - `openai`：走 `/chat/completions`
    - `auto`（默认）：先用 openai，400/404 时回退 anthropic
    """
    protocol = os.environ.get("CONSTRAINT_LLM_PROTOCOL", "auto").lower()
    if protocol == "anthropic":
        return anthropic_messages_chat
    if protocol == "openai":
        return openai_compatible_chat
    if protocol == "auto":
        # auto 由 llm_trader_from_env 实际探测后绑定，见该函数
        return openai_compatible_chat
    raise LLMError(
        f"未知协议 {protocol!r}，可选：anthropic / openai / auto"
    )


# ── 输出解析 ───────────────────────────────────────────────
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
def make_llm_trader(
    chat_fn: ChatFn | None = None,
    *,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 512,
    timeout_s: float = 30.0,
    rules_in_prompt: bool = True,
    cache: bool = True,
    max_calls: int | None = None,
) -> Callable[[dict, dict], dict]:
    """构造一个可作为 `trader_fn` 使用的 LLM 交易员。

    Args:
        chat_fn: 调用函数 `(messages, temperature, max_tokens) -> str`。
                 省略时使用 `openai_compatible_chat`。
        model: 模型标识，用于报告与 config_hash。
        temperature: 必须为 0 才能保证可复现。
        max_calls: 实际调用上限（不含缓存命中），超出即抛 LLMError。
        rules_in_prompt: 是否把交易约束写进系统提示词。
        cache: 同一输入是否复用上次决策。
    """
    _cache: dict[str, dict] = {}
    calls = {"n": 0}

    def trader(perturbed_input: dict, config: dict[str, Any]) -> dict[str, Any]:
        key = hashlib.sha256(
            json.dumps(perturbed_input, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]

        if cache and key in _cache:
            _STATS["cache_hits"] += 1
            return dict(_cache[key])

        if max_calls is not None and calls["n"] >= max_calls:
            raise LLMError(
                f"已达调用上限 {max_calls}，中止评测以避免超预算"
            )

        system = build_system_prompt(config, rules_in_prompt)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": render_user_prompt(perturbed_input)},
        ]

        started = time.monotonic()
        try:
            text = (
                chat_fn(messages, temperature, max_tokens)
                if chat_fn
                else openai_compatible_chat(
                    messages, temperature, max_tokens, timeout_s=timeout_s
                )
            )
        except LLMError:
            _STATS["api_errors"] += 1
            raise
        finally:
            _STATS["elapsed_s"] += time.monotonic() - started

        _STATS["calls"] += 1
        calls["n"] += 1

        decision = parse_decision(text, perturbed_input)
        if cache:
            _cache[key] = decision
        return dict(decision)

    trader.__llm__ = {  # type: ignore[attr-defined]
        "model": model,
        "temperature": temperature,
        "rules_in_prompt": rules_in_prompt,
        "cache": cache,
        "max_calls": max_calls,
    }
    return trader


def _probe_auto_chat() -> ChatFn:
    """auto 模式：探测端点支持哪种协议，返回可用的 chat_fn。

    很多本机代理只暴露 Anthropic `/v1/messages`，而云厂商多半只给
    OpenAI `/chat/completions`。这里用一次最小请求实测，避免让使用者猜。
    """
    base_url = os.environ.get("CONSTRAINT_LLM_BASE_URL", "").rstrip("/")
    model = os.environ.get("CONSTRAINT_LLM_MODEL", "")
    if not base_url or not model:
        raise LLMError(
            "缺少 LLM 配置：请设置 CONSTRAINT_LLM_BASE_URL 与 CONSTRAINT_LLM_MODEL"
        )
    anthropic_key = (
        os.environ.get("CONSTRAINT_LLM_API_KEY")
        or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    )
    openai_key = os.environ.get("CONSTRAINT_LLM_API_KEY", "")

    probe_msgs = [{"role": "user", "content": "ping"}]

    # 先试 Anthropic：本机代理场景更常见
    try:
        anthropic_messages_chat(
            probe_msgs, 0.0, 16,
            base_url=base_url, api_key=anthropic_key, model=model, timeout_s=15,
        )
        return anthropic_messages_chat
    except LLMError:
        pass

    try:
        openai_compatible_chat(
            probe_msgs, 0.0, 16,
            base_url=base_url, api_key=openai_key, model=model, timeout_s=15,
        )
        return openai_compatible_chat
    except LLMError as e:
        raise LLMError(
            f"自动探测失败：端点既不支持 Anthropic /v1/messages 也不支持 "
            f"OpenAI /chat/completions（{e}）"
        ) from e


def llm_trader_from_env(
    chat_fn: ChatFn | None = None,
) -> Callable[[dict, dict], dict]:
    """按环境变量构造 LLM 交易员，供 `trader=llm` 使用。

    环境变量：
        CONSTRAINT_LLM_BASE_URL   端点根地址（必填）
        CONSTRAINT_LLM_MODEL      模型名（必填）
        CONSTRAINT_LLM_PROTOCOL   anthropic / openai / auto（默认 auto，自动探测）
        CONSTRAINT_LLM_API_KEY    鉴权密钥；缺省时自动读取 ANTHROPIC_AUTH_TOKEN
        CONSTRAINT_LLM_TEMPERATURE  温度，默认 0
        CONSTRAINT_LLM_MAX_TOKENS  单次最大输出，默认 512
        CONSTRAINT_LLM_TIMEOUT_S   单次超时，默认 60
        CONSTRAINT_LLM_MAX_CALLS   总调用上限（可选）
        CONSTRAINT_LLM_RULES_IN_PROMPT  默认 true
        CONSTRAINT_LLM_CACHE      默认 true
    """
    env = os.environ
    model = env.get("CONSTRAINT_LLM_MODEL") or ""

    base_url = env.get("CONSTRAINT_LLM_BASE_URL") or ""
    api_key = (
        env.get("CONSTRAINT_LLM_API_KEY")
        or env.get("ANTHROPIC_AUTH_TOKEN")
        or ""
    )
    if not base_url:
        raise LLMError(
            "缺少 LLM 配置：请设置 CONSTRAINT_LLM_BASE_URL"
        )
    if not model:
        raise LLMError("缺少 LLM 配置：请设置 CONSTRAINT_LLM_MODEL")

    protocol = env.get("CONSTRAINT_LLM_PROTOCOL", "auto").lower()
    if chat_fn is None:
        if protocol == "auto":
            resolved = _probe_auto_chat()
        elif protocol == "anthropic":
            resolved = anthropic_messages_chat
        elif protocol == "openai":
            resolved = openai_compatible_chat
        else:
            raise LLMError(
                f"未知协议 {protocol!r}，可选：anthropic / openai / auto"
            )
        chat_fn = _bind(resolved, base_url, api_key, model, env)

    return make_llm_trader(
        chat_fn,
        model=model or None,
        temperature=float(env.get("CONSTRAINT_LLM_TEMPERATURE", "0")),
        max_tokens=int(env.get("CONSTRAINT_LLM_MAX_TOKENS", "512")),
        timeout_s=float(env.get("CONSTRAINT_LLM_TIMEOUT_S", "60")),
        rules_in_prompt=env.get("CONSTRAINT_LLM_RULES_IN_PROMPT", "true").lower()
        in ("1", "true", "yes"),
        cache=env.get("CONSTRAINT_LLM_CACHE", "true").lower() in ("1", "true", "yes"),
        max_calls=(
            int(env["CONSTRAINT_LLM_MAX_CALLS"])
            if env.get("CONSTRAINT_LLM_MAX_CALLS")
            else None
        ),
    )


def _bind(
    fn: ChatFn, base_url: str, api_key: str, model: str, env: Any
) -> ChatFn:
    """把端点/密钥/模型绑定到 chat 函数上，供 make_llm_trader 直接调用。"""

    def bound(messages: list[dict], temperature: float, max_tokens: int) -> str:
        return fn(  # type: ignore[call-arg]
            messages,
            temperature,
            max_tokens,
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_s=float(env.get("CONSTRAINT_LLM_TIMEOUT_S", "60")),
        )

    return bound