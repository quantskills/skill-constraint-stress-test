"""PandaData MCP 信封解析器 — 纯标准库，可独立测试。

数据由阅读本 skill 的 AI 通过 MCP 工具取回并落盘（JSON），本模块只负责：

1. 解析 MCP 工具 `call_pandadata` 的两种返回形态
   - 表格型：``{"type": "dataframe", "columns": [...], "rows": [[...]], ...}``
   - 普通列表 / 字典：如 ``[{"date": "..."}]`` 或 ``{"date": "..."}``
2. 校验 ``ok`` / ``truncated`` / 必需列，抛结构化的 :class:`MCPError`
3. 读取导出目录或单文件，按方法名归类

**不联网、不依赖任何第三方库。** 评测执行阶段完全不会调用本模块的外部能力。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = [
    "MCPError",
    "parse_envelope",
    "extract_result",
    "check_not_truncated",
    "require_columns",
    "result_records",
    "parse_suspended",
    "parse_st",
    "parse_trade_list",
    "parse_stock_daily",
    "load_exports",
    "EXPORT_METHODS",
]


class MCPError(Exception):
    """MCP 返回不可用时的结构化错误。

    属性：
        code:    机器可读错误码（``mcp_ok_false`` / ``mcp_truncated`` / ...）
        message: 人类可读描述
        hint:    下一步该怎么做（通常是「缩小日期区间后重新调用」）
        details: 附加诊断信息
    """

    def __init__(
        self,
        code: str,
        message: str,
        hint: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "details": self.details,
        }

    def __str__(self) -> str:  # pragma: no cover - 仅影响打印
        base = f"[{self.code}] {self.message}"
        return f"{base}  → {self.hint}" if self.hint else base


# ---------------------------------------------------------------------------
# 信封解析
# ---------------------------------------------------------------------------


def _looks_like_envelope(payload: Any) -> bool:
    return isinstance(payload, dict) and "ok" in payload


def parse_envelope(payload: Any) -> dict[str, Any]:
    """校验 ``call_pandadata`` 外层信封，返回 ``{ok, method, params, result, request_id}``。

    允许直接传入裸 ``result``（没有 ``ok`` 字段）：此时包一层 ``ok=True``，
    便于调用方混合使用「完整信封」与「已剥离的 result」。

    注意：``ok=false`` 无例外地抛错——绝不能把错误响应当数据用。
    """
    if _looks_like_envelope(payload):
        if not isinstance(payload["ok"], bool):
            raise MCPError(
                "mcp_invalid_shape",
                "envelope field 'ok' must be a boolean",
                hint="Pass the raw call_pandadata response unchanged.",
                details={"ok": repr(payload.get("ok"))},
            )
        if not payload["ok"]:
            err = payload.get("error")
            if isinstance(err, dict):
                msg = str(err.get("message") or err.get("code") or "MCP call failed")
                code = str(err.get("code") or "mcp_ok_false")
            else:
                msg = str(err or payload.get("message") or "MCP call failed")
                code = "mcp_ok_false"
            raise MCPError(
                code,
                msg,
                hint="Check auth_status first, then re-issue the tool call.",
                details={"request_id": payload.get("request_id")},
            )
        if "result" not in payload:
            raise MCPError(
                "mcp_invalid_shape",
                "envelope is ok but has no 'result' field",
                hint="Re-issue the call and save the full response JSON.",
            )
        return payload

    # 裸 result：包一层信封
    return {
        "ok": True,
        "method": None,
        "params": {},
        "result": payload,
        "request_id": None,
    }


def extract_result(payload: Any) -> Any:
    """返回 ``result`` 本体（自动处理完整信封 / 裸 result）。"""
    return parse_envelope(payload)["result"]


def _is_dataframe(result: Any) -> bool:
    return (
        isinstance(result, dict)
        and result.get("type") == "dataframe"
        and "columns" in result
        and "rows" in result
    )


def check_not_truncated(payload: Any, *, allow_truncated: bool = False) -> None:
    """``truncated=true`` 时抛 :class:`MCPError`。

    这是本 skill 最重要的一条防御：实测中 240 行的分钟线只回了 180 行
    （``truncation_reasons: ["byte_limit"]``）。被截断的数据集如果当完整数据用，
    会静默改变评测结论，因此默认一律拒绝。
    """
    if allow_truncated:
        return
    result = extract_result(payload)
    if not _is_dataframe(result):
        return
    if result.get("truncated") is True:
        reasons = result.get("truncation_reasons") or ["unknown"]
        got = len(result.get("rows") or [])
        total = result.get("total_rows", result.get("row_count"))
        raise MCPError(
            "mcp_truncated",
            f"MCP result is truncated ({', '.join(str(r) for r in reasons)}): "
            f"received {got} of {total} rows",
            hint=(
                "Split into narrower date ranges or smaller symbol batches, "
                "call the tool again per batch, and export each response; "
                "never treat truncated rows as the complete dataset."
            ),
            details={
                "truncation_reasons": reasons,
                "received_rows": got,
                "total_rows": total,
            },
        )


def require_columns(payload: Any, required: list[str] | tuple[str, ...]) -> list[str]:
    """表格型结果缺列时抛错，返回实际列名。"""
    result = extract_result(payload)
    if not _is_dataframe(result):
        raise MCPError(
            "mcp_invalid_shape",
            "expected a dataframe-shaped result (type/columns/rows)",
            hint="Call get_method_doc to confirm the method returns a table.",
            details={"result_type": type(result).__name__},
        )
    columns = result.get("columns") or []
    if not isinstance(columns, list):
        raise MCPError(
            "mcp_invalid_shape",
            "dataframe 'columns' must be a list",
            details={"columns": repr(columns)},
        )
    missing = [c for c in required if c not in columns]
    if missing:
        raise MCPError(
            "mcp_missing_columns",
            f"dataframe is missing required columns: {', '.join(missing)}",
            hint=(
                "Request them explicitly via the 'fields' argument, "
                "or check get_method_doc for the supported column names."
            ),
            details={"missing": missing, "available": columns},
        )
    return [str(c) for c in columns]


def result_records(
    payload: Any,
    *,
    required_columns: list[str] | tuple[str, ...] | None = None,
    allow_truncated: bool = False,
) -> list[dict[str, Any]]:
    """把两种返回形态统一成 ``list[dict]``。

    - 表格型：按 ``columns`` 把 ``rows`` 的 list 转成 dict（行长度不符即报错）
    - 列表型：必须每项都是 dict
    - 字典型：包成单元素列表
    """
    check_not_truncated(payload, allow_truncated=allow_truncated)
    result = extract_result(payload)

    if _is_dataframe(result):
        columns = require_columns(payload, required_columns or [])
        rows = result.get("rows")
        if not isinstance(rows, list):
            raise MCPError(
                "mcp_invalid_shape",
                "dataframe 'rows' must be a list",
                details={"rows_type": type(rows).__name__},
            )
        records: list[dict[str, Any]] = []
        for idx, row in enumerate(rows):
            if not isinstance(row, list):
                raise MCPError(
                    "mcp_invalid_shape",
                    f"dataframe row #{idx} is not a list",
                    details={"row_index": idx, "row_type": type(row).__name__},
                )
            if len(row) != len(columns):
                raise MCPError(
                    "mcp_invalid_shape",
                    f"dataframe row #{idx} has {len(row)} cells for {len(columns)} columns",
                    details={"row_index": idx, "columns": columns},
                )
            records.append(dict(zip(columns, row)))
        return records

    if isinstance(result, list):
        for idx, item in enumerate(result):
            if not isinstance(item, dict):
                raise MCPError(
                    "mcp_invalid_shape",
                    f"list result item #{idx} is not an object",
                    hint="Expected rows like {\"date\": ..., \"symbol\": ...}.",
                    details={"item_index": idx, "item_type": type(item).__name__},
                )
        return list(result)

    if isinstance(result, dict):
        return [result]

    raise MCPError(
        "mcp_invalid_shape",
        f"unsupported result type: {type(result).__name__}",
        details={"result_type": type(result).__name__},
    )


# ---------------------------------------------------------------------------
# 字段归一化
# ---------------------------------------------------------------------------


def _norm_date(value: Any) -> str:
    """把日期单元格归一成 ``YYYYMMDD`` 字符串。

    网关可能回 ``20251110`` / ``"20251110"`` / ``20251110.0``，
    直接 str() 会得到 ``20251110.0``，所以这里显式处理浮点。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "t"}:
        return True
    if text in {"false", "0", "no", "n", "f", ""}:
        return False
    return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _flag_table(
    payload: Any,
    flag_column: str,
    required: list[str],
) -> list[dict[str, Any]]:
    records = result_records(payload, required_columns=required)
    out: list[dict[str, Any]] = []
    for record in records:
        symbol = record.get("symbol")
        if symbol is None or str(symbol).strip() == "":
            raise MCPError(
                "mcp_missing_columns",
                "record has an empty 'symbol' cell",
                hint="Check that 'symbol' is included in the requested fields.",
                details={"record": {k: repr(v) for k, v in record.items()}},
            )
        flag = _as_bool(record.get(flag_column))
        if flag is None:
            raise MCPError(
                "mcp_invalid_shape",
                f"column '{flag_column}' is not boolean-parseable",
                details={"value": repr(record.get(flag_column)), "symbol": str(symbol)},
            )
        out.append(
            {
                "symbol": str(symbol).strip(),
                "date": _norm_date(record.get("date")),
                flag_column: flag,
            }
        )
    return out


# ---------------------------------------------------------------------------
# 面向 4 个方法的类型化解析
# ---------------------------------------------------------------------------


def parse_suspended(payload: Any, *, only_flagged: bool = True) -> list[dict[str, Any]]:
    """解析 ``get_suspended_list``：列 ``symbol,date,is_suspended``。"""
    rows = _flag_table(payload, "is_suspended", ["symbol", "date", "is_suspended"])
    return [r for r in rows if r["is_suspended"]] if only_flagged else rows


def parse_st(payload: Any, *, only_flagged: bool = True) -> list[dict[str, Any]]:
    """解析 ``get_st_list``：列 ``symbol,date,is_st``。"""
    rows = _flag_table(payload, "is_st", ["symbol", "date", "is_st"])
    return [r for r in rows if r["is_st"]] if only_flagged else rows


def parse_trade_list(payload: Any) -> list[dict[str, Any]]:
    """解析 ``get_trade_list``：列表 ``[{"date": ..., "symbol": ...}, ...]``。"""
    records = result_records(payload, required_columns=["date", "symbol"])
    out: list[dict[str, Any]] = []
    for record in records:
        symbol = record.get("symbol")
        if symbol is None or str(symbol).strip() == "":
            raise MCPError(
                "mcp_missing_columns",
                "trade-list record has an empty 'symbol'",
                details={"record": {k: repr(v) for k, v in record.items()}},
            )
        out.append({"date": _norm_date(record.get("date")), "symbol": str(symbol).strip()})
    return out


def parse_stock_daily(payload: Any) -> list[dict[str, Any]]:
    """解析 ``get_stock_daily``：列含 ``date,symbol,open,high,low,close``。"""
    records = result_records(
        payload, required_columns=["date", "symbol", "open", "high", "low", "close"]
    )
    out: list[dict[str, Any]] = []
    for record in records:
        symbol = record.get("symbol")
        if symbol is None or str(symbol).strip() == "":
            raise MCPError(
                "mcp_missing_columns",
                "stock-daily record has an empty 'symbol'",
                details={"record": {k: repr(v) for k, v in record.items()}},
            )
        out.append(
            {
                "date": _norm_date(record.get("date")),
                "symbol": str(symbol).strip(),
                "open": _as_float(record.get("open")),
                "high": _as_float(record.get("high")),
                "low": _as_float(record.get("low")),
                "close": _as_float(record.get("close")),
                "pre_close": _as_float(record.get("pre_close")),
                "limit_up": _as_float(record.get("limit_up")),
                "limit_down": _as_float(record.get("limit_down")),
                "name": record.get("name"),
            }
        )
    return out


# ---------------------------------------------------------------------------
# 导出文件加载
# ---------------------------------------------------------------------------

#: 导出键 → 规范方法名
EXPORT_METHODS: dict[str, str] = {
    "suspended": "get_suspended_list",
    "get_suspended_list": "get_suspended_list",
    "st": "get_st_list",
    "get_st_list": "get_st_list",
    "trade_list": "get_trade_list",
    "get_trade_list": "get_trade_list",
    "stock_daily": "get_stock_daily",
    "get_stock_daily": "get_stock_daily",
}

_SHORT_NAME: dict[str, str] = {
    "get_suspended_list": "suspended",
    "get_st_list": "st",
    "get_trade_list": "trade_list",
    "get_stock_daily": "stock_daily",
}


def _filename_key(path: Path) -> str | None:
    stem = path.stem.strip().lower().replace("-", "_")
    for prefix in ("mcp_", "export_", "result_"):
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
    # 去掉尾部的日期 / 序号后缀：stock_daily_20251110 → stock_daily
    parts = stem.split("_")
    while len(parts) > 1 and (parts[-1].isdigit() or parts[-1] in {"export", "result", "part"}):
        parts.pop()
    stem = "_".join(parts)
    return EXPORT_METHODS.get(stem)


def _collect(payload: Any, into: dict[str, list[Any]], default_key: str | None) -> None:
    if isinstance(payload, list) and payload and all(
        isinstance(item, dict) and "method" in item and "ok" in item for item in payload
    ):
        for item in payload:
            _collect(item, into, None)
        return

    if _looks_like_envelope(payload):
        parse_envelope(payload)  # ok=false 立即抛错
        key = EXPORT_METHODS.get(str(payload.get("method") or "").lower())
        if key is None:
            key = default_key
        if key is None:
            raise MCPError(
                "mcp_unknown_method",
                f"export contains an unsupported method: {payload.get('method')!r}",
                hint="Only get_suspended_list / get_st_list / get_trade_list / get_stock_daily are used.",
            )
        into.setdefault(_SHORT_NAME[key], []).append(payload["result"])
        return

    if isinstance(payload, dict) and "ok" not in payload and _has_export_keys(payload):
        # 单文件多方法：{"suspended": <envelope|result>, ...}
        for raw_key, value in payload.items():
            key = EXPORT_METHODS.get(str(raw_key).strip().lower())
            if key is None:
                continue
            _collect(value, into, key)
        return

    if isinstance(payload, dict) and "ok" not in payload and default_key is not None:
        into.setdefault(_SHORT_NAME[default_key], []).append(payload)
        return

    if default_key is None:
        raise MCPError(
            "mcp_invalid_shape",
            "cannot determine which method a bare result belongs to",
            hint="Save each response as <method>.json (e.g. stock_daily.json) or keep the 'method' field.",
        )
    into.setdefault(_SHORT_NAME[default_key], []).append(payload)


def _has_export_keys(payload: dict[str, Any]) -> bool:
    return any(str(k).strip().lower() in EXPORT_METHODS for k in payload)


def load_exports(source: Path | str) -> dict[str, list[Any]]:
    """读取导出目录或单文件，返回 ``{短名: [result, ...]}``。

    支持三种落盘方式（都可混合、可多份分片）：

    1. **目录**：``suspended.json`` / ``st.json`` / ``trade_list.json`` /
       ``stock_daily.json``（也认 ``get_*`` 全名）
    2. **单文件多方法**：``{"suspended": {...}, "trade_list": {...}}``
    3. **单文件数组**：``[{"ok": true, "method": "get_st_list", "result": ...}, ...]``
       ——适合把同一方法的多个日期分片顺序写进一个数组。
    """
    path = Path(source)
    if not path.exists():
        raise MCPError(
            "mcp_export_not_found",
            f"MCP export path does not exist: {path}",
            hint="Run the MCP calls first and save each response as JSON under this path.",
        )

    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    if not files:
        raise MCPError(
            "mcp_export_not_found",
            f"No *.json export files under {path}",
            hint="Save at least one MCP response JSON in this directory.",
        )

    exports: dict[str, list[Any]] = {}
    for file in files:
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MCPError(
                "mcp_export_unreadable",
                f"cannot read export file {file.name}: {exc}",
                hint="Save the raw tool response as-is, without hand-editing.",
            ) from exc
        default_key = _filename_key(file)
        _collect(payload, exports, default_key)

    if not exports:
        raise MCPError(
            "mcp_export_empty",
            f"No recognised PandaData method exports under {path}",
            hint="Expected suspended / st / trade_list / stock_daily JSON exports.",
        )
    return exports