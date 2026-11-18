"""`scripts/mcp_source.py` 的单元测试 — 全部离线，不发起任何网络请求。

覆盖两种 MCP 返回形态、截断、缺列、ok=false、导出文件加载。
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from scripts import mcp_source as m  # noqa: E402


# ---------------------------------------------------------------------------
# 测试数据构造
# ---------------------------------------------------------------------------


def envelope(method, result, ok=True, request_id="req-1"):
    return {
        "ok": ok,
        "method": method,
        "params": {},
        "result": result,
        "request_id": request_id,
    }


def dataframe(columns, rows, truncated=False, reasons=None):
    return {
        "type": "dataframe",
        "columns": list(columns),
        "rows": [list(r) for r in rows],
        "row_count": len(rows),
        "total_rows": len(rows),
        "truncated": truncated,
        "truncation_reasons": reasons or [],
    }


SUSPENDED_DF = dataframe(
    ["symbol", "date", "is_suspended"],
    [
        ["000407.SZ", "20251110", True],
        ["000851.SZ", "20251110", True],
        ["000001.SZ", "20251110", False],
    ],
)

ST_DF = dataframe(
    ["symbol", "date", "is_st"],
    [["000004.SZ", "20251110", True], ["000001.SZ", "20251110", False]],
)

TRADE_LIST = [{"date": "20251110", "symbol": "000001.SZ"}]

DAILY_DF = dataframe(
    ["date", "symbol", "open", "high", "low", "close", "pre_close"],
    [
        ["20251110", "000001.SZ", 10.0, 11.63, 10.0, 11.63, 10.57],
        ["20251110", "000407.SZ", 3.5, 3.78, 3.5, 3.7, 3.44],
    ],
)


# ---------------------------------------------------------------------------
# parse_envelope
# ---------------------------------------------------------------------------


def test_parse_envelope_ok_returns_envelope():
    payload = envelope("get_trade_list", TRADE_LIST)
    parsed = m.parse_envelope(payload)
    assert parsed["ok"] is True
    assert parsed["result"] == TRADE_LIST


def test_parse_envelope_bare_result_is_wrapped():
    parsed = m.parse_envelope(TRADE_LIST)
    assert parsed["ok"] is True
    assert parsed["result"] == TRADE_LIST


def test_parse_envelope_ok_false_raises():
    payload = envelope("get_trade_list", None, ok=False)
    with pytest.raises(m.MCPError) as exc:
        m.parse_envelope(payload)
    assert exc.value.code == "mcp_ok_false"
    assert "auth_status" in exc.value.hint


def test_parse_envelope_ok_false_with_error_object():
    payload = {
        "ok": False,
        "method": "get_stock_daily",
        "error": {"code": "upstream_error", "message": "gateway timeout"},
        "request_id": "req-9",
    }
    with pytest.raises(m.MCPError) as exc:
        m.parse_envelope(payload)
    assert exc.value.code == "upstream_error"
    assert "gateway timeout" in exc.value.message
    assert exc.value.details["request_id"] == "req-9"


def test_parse_envelope_ok_not_boolean_raises():
    with pytest.raises(m.MCPError) as exc:
        m.parse_envelope({"ok": "true", "result": TRADE_LIST})
    assert exc.value.code == "mcp_invalid_shape"


def test_parse_envelope_ok_true_without_result_raises():
    with pytest.raises(m.MCPError) as exc:
        m.parse_envelope({"ok": True})
    assert exc.value.code == "mcp_invalid_shape"


def test_extract_result_unwraps():
    assert m.extract_result(envelope("get_trade_list", TRADE_LIST)) == TRADE_LIST


# ---------------------------------------------------------------------------
# 截断
# ---------------------------------------------------------------------------


def test_truncated_dataframe_is_rejected():
    df = dataframe(
        ["date", "symbol", "open", "high", "low", "close"],
        [["20251110", "000001.SZ", 1, 2, 1, 2]],
        truncated=True,
        reasons=["byte_limit"],
    )
    with pytest.raises(m.MCPError) as exc:
        m.check_not_truncated(envelope("get_stock_daily", df))
    assert exc.value.code == "mcp_truncated"
    assert "byte_limit" in exc.value.message
    assert exc.value.details["truncation_reasons"] == ["byte_limit"]


def test_truncated_can_be_explicitly_allowed():
    df = dataframe(
        ["symbol", "date", "is_suspended"],
        [["000407.SZ", "20251110", True]],
        truncated=True,
        reasons=["row_limit"],
    )
    m.check_not_truncated(envelope("get_suspended_list", df), allow_truncated=True)
    records = m.result_records(
        envelope("get_suspended_list", df), allow_truncated=True
    )
    assert records == [{"symbol": "000407.SZ", "date": "20251110", "is_suspended": True}]


def test_result_records_rejects_truncated_by_default():
    df = dataframe(
        ["symbol", "date", "is_suspended"],
        [["000407.SZ", "20251110", True]],
        truncated=True,
        reasons=["byte_limit"],
    )
    with pytest.raises(m.MCPError) as exc:
        m.result_records(envelope("get_suspended_list", df))
    assert exc.value.code == "mcp_truncated"


def test_non_dataframe_truncated_flag_is_ignored():
    # 普通列表形态没有 truncated 字段，不应误报
    m.check_not_truncated(envelope("get_trade_list", TRADE_LIST))


# ---------------------------------------------------------------------------
# 缺列 / 畸形
# ---------------------------------------------------------------------------


def test_require_columns_reports_missing():
    df = dataframe(["symbol", "date"], [["000001.SZ", "20251110"]])
    with pytest.raises(m.MCPError) as exc:
        m.require_columns(envelope("get_st_list", df), ["symbol", "date", "is_st"])
    assert exc.value.code == "mcp_missing_columns"
    assert exc.value.details["missing"] == ["is_st"]


def test_require_columns_on_non_table_raises():
    with pytest.raises(m.MCPError) as exc:
        m.require_columns(envelope("get_trade_list", TRADE_LIST), ["symbol"])
    assert exc.value.code == "mcp_invalid_shape"


def test_result_records_rejects_row_length_mismatch():
    df = dataframe(["symbol", "date"], [["000001.SZ"]])
    with pytest.raises(m.MCPError) as exc:
        m.result_records(envelope("get_st_list", df))
    assert exc.value.code == "mcp_invalid_shape"
    assert exc.value.details["row_index"] == 0


def test_result_records_rejects_non_dict_list_items():
    with pytest.raises(m.MCPError) as exc:
        m.result_records(envelope("get_trade_list", ["000001.SZ"]))
    assert exc.value.code == "mcp_invalid_shape"


def test_result_records_rejects_unsupported_type():
    with pytest.raises(m.MCPError) as exc:
        m.result_records(envelope("get_trade_list", 42))
    assert exc.value.code == "mcp_invalid_shape"


# ---------------------------------------------------------------------------
# 类型化解析：普通列表 / 字典形态
# ---------------------------------------------------------------------------


def test_parse_trade_list_plain_list():
    rows = m.parse_trade_list(envelope("get_trade_list", TRADE_LIST))
    assert rows == [{"date": "20251110", "symbol": "000001.SZ"}]


def test_parse_trade_list_accepts_dataframe_shape():
    df = dataframe(["date", "symbol"], [["20251110", "600000.SH"]])
    rows = m.parse_trade_list(envelope("get_trade_list", df))
    assert rows == [{"date": "20251110", "symbol": "600000.SH"}]


def test_parse_trade_list_empty_symbol_raises():
    payload = envelope("get_trade_list", [{"date": "20251110", "symbol": ""}])
    with pytest.raises(m.MCPError) as exc:
        m.parse_trade_list(payload)
    assert exc.value.code == "mcp_missing_columns"


def test_parse_prev_trade_date_style_dict_via_result_records():
    # get_prev_trade_date 返回 {"date": "20251107"} —— 字典型结果包成单元素列表
    payload = envelope("get_prev_trade_date", {"date": "20251107"})
    assert m.result_records(payload) == [{"date": "20251107"}]


# ---------------------------------------------------------------------------
# 类型化解析：表格形态
# ---------------------------------------------------------------------------


def test_parse_suspended_filters_flagged_only():
    rows = m.parse_suspended(envelope("get_suspended_list", SUSPENDED_DF))
    assert [r["symbol"] for r in rows] == ["000407.SZ", "000851.SZ"]
    assert all(r["is_suspended"] is True for r in rows)


def test_parse_suspended_can_keep_all_rows():
    rows = m.parse_suspended(envelope("get_suspended_list", SUSPENDED_DF), only_flagged=False)
    assert len(rows) == 3


def test_parse_suspended_requires_columns():
    df = dataframe(["symbol", "date"], [["000407.SZ", "20251110"]])
    with pytest.raises(m.MCPError) as exc:
        m.parse_suspended(envelope("get_suspended_list", df))
    assert exc.value.code == "mcp_missing_columns"


def test_parse_suspended_rejects_unparseable_flag():
    df = dataframe(
        ["symbol", "date", "is_suspended"], [["000407.SZ", "20251110", "maybe"]]
    )
    with pytest.raises(m.MCPError) as exc:
        m.parse_suspended(envelope("get_suspended_list", df))
    assert exc.value.code == "mcp_invalid_shape"


def test_parse_st_filters_flagged_only():
    rows = m.parse_st(envelope("get_st_list", ST_DF))
    assert rows == [{"symbol": "000004.SZ", "date": "20251110", "is_st": True}]


def test_parse_st_bool_from_numeric_cell():
    df = dataframe(["symbol", "date", "is_st"], [["000004.SZ", "20251110", 1]])
    rows = m.parse_st(envelope("get_st_list", df))
    assert rows[0]["is_st"] is True


def test_parse_stock_daily_normalizes_dates_and_numbers():
    rows = m.parse_stock_daily(envelope("get_stock_daily", DAILY_DF))
    assert rows[0]["date"] == "20251110"
    assert rows[0]["close"] == pytest.approx(11.63)
    assert rows[0]["pre_close"] == pytest.approx(10.57)
    assert rows[1]["symbol"] == "000407.SZ"


def test_parse_stock_daily_handles_float_dates():
    df = dataframe(
        ["date", "symbol", "open", "high", "low", "close"],
        [[20251110.0, "000001.SZ", 1, 2, 1, 2]],
    )
    rows = m.parse_stock_daily(envelope("get_stock_daily", df))
    assert rows[0]["date"] == "20251110"


def test_parse_stock_daily_missing_ohlc_raises():
    df = dataframe(["date", "symbol", "open", "high", "low"], [["20251110", "000001.SZ", 1, 2, 1]])
    with pytest.raises(m.MCPError) as exc:
        m.parse_stock_daily(envelope("get_stock_daily", df))
    assert exc.value.code == "mcp_missing_columns"
    assert "close" in exc.value.details["missing"]


def test_parse_stock_daily_truncated_raises():
    df = dataframe(
        ["date", "symbol", "open", "high", "low", "close"],
        [["20251110", "000001.SZ", 1, 2, 1, 2]],
        truncated=True,
        reasons=["byte_limit"],
    )
    with pytest.raises(m.MCPError) as exc:
        m.parse_stock_daily(envelope("get_stock_daily", df))
    assert exc.value.code == "mcp_truncated"


# ---------------------------------------------------------------------------
# 导出文件加载
# ---------------------------------------------------------------------------


def _write_json(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_exports_from_directory(tmp_path):
    _write_json(tmp_path / "suspended.json", envelope("get_suspended_list", SUSPENDED_DF))
    _write_json(tmp_path / "st.json", envelope("get_st_list", ST_DF))
    _write_json(tmp_path / "trade_list.json", envelope("get_trade_list", TRADE_LIST))

    exports = m.load_exports(tmp_path)
    assert set(exports) == {"suspended", "st", "trade_list"}
    assert len(exports["suspended"]) == 1


def test_load_exports_accepts_full_method_filenames(tmp_path):
    _write_json(tmp_path / "get_stock_daily.json", envelope("get_stock_daily", DAILY_DF))
    exports = m.load_exports(tmp_path)
    assert "stock_daily" in exports


def test_load_exports_accepts_dated_filename_suffix(tmp_path):
    _write_json(
        tmp_path / "stock_daily_20251110.json",
        envelope("get_stock_daily", DAILY_DF),
    )
    exports = m.load_exports(tmp_path)
    assert len(exports["stock_daily"]) == 1


def test_load_exports_single_file_uses_filename_hint(tmp_path):
    payload = SUSPENDED_DF  # 裸 result，靠文件名判断方法
    path = _write_json(tmp_path / "suspended.json", payload)
    exports = m.load_exports(path)
    assert len(exports["suspended"]) == 1


def test_load_exports_multi_method_single_file(tmp_path):
    path = _write_json(
        tmp_path / "bundle.json",
        {
            "suspended": envelope("get_suspended_list", SUSPENDED_DF),
            "trade_list": envelope("get_trade_list", TRADE_LIST),
        },
    )
    exports = m.load_exports(path)
    assert set(exports) == {"suspended", "trade_list"}


def test_load_exports_array_of_envelopes(tmp_path):
    path = _write_json(
        tmp_path / "bundle.json",
        [
            envelope("get_st_list", ST_DF),
            envelope("get_st_list", ST_DF),
        ],
    )
    exports = m.load_exports(path)
    assert len(exports["st"]) == 2


def test_load_exports_missing_path_raises(tmp_path):
    with pytest.raises(m.MCPError) as exc:
        m.load_exports(tmp_path / "nope")
    assert exc.value.code == "mcp_export_not_found"


def test_load_exports_empty_directory_raises(tmp_path):
    with pytest.raises(m.MCPError) as exc:
        m.load_exports(tmp_path)
    assert exc.value.code == "mcp_export_not_found"


def test_load_exports_unreadable_json_raises(tmp_path):
    (tmp_path / "suspended.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(m.MCPError) as exc:
        m.load_exports(tmp_path)
    assert exc.value.code == "mcp_export_unreadable"


def test_load_exports_unknown_method_raises(tmp_path):
    payload = envelope("get_future_daily", [{"symbol": "RB"}])
    path = _write_json(tmp_path / "unknown.json", payload)
    with pytest.raises(m.MCPError) as exc:
        m.load_exports(path)
    assert exc.value.code == "mcp_unknown_method"


def test_load_exports_propagates_ok_false(tmp_path):
    path = _write_json(
        tmp_path / "suspended.json",
        envelope("get_suspended_list", None, ok=False),
    )
    with pytest.raises(m.MCPError) as exc:
        m.load_exports(path)
    assert exc.value.code == "mcp_ok_false"


def test_load_exports_no_recognised_method_raises(tmp_path):
    path = _write_json(tmp_path / "misc.json", {"hello": "world"})
    with pytest.raises(m.MCPError) as exc:
        m.load_exports(path)
    assert exc.value.code in {"mcp_export_empty", "mcp_invalid_shape"}


# ---------------------------------------------------------------------------
# 结构化错误
# ---------------------------------------------------------------------------


def test_mcp_error_to_dict_and_str():
    err = m.MCPError("mcp_truncated", "too many rows", hint="split date range")
    assert err.to_dict()["code"] == "mcp_truncated"
    assert "split date range" in str(err)
    assert err.details == {}

# ---------------------------------------------------------------------------
# fixture_generator 消费 MCP 导出（离线，不联网）
# ---------------------------------------------------------------------------


def test_fixture_generator_consumes_exports_without_network(tmp_path):
    from scripts import fixture_generator as fg

    exports = {
        "suspended": [SUSPENDED_DF],
        "st": [ST_DF],
        "trade_list": [TRADE_LIST],
        "stock_daily": [DAILY_DF],
    }
    out = tmp_path / "generated"
    out.mkdir()

    susp = fg.generate_suspended_fixture(exports, "20251110", out)
    st = fg.generate_st_fixture(exports, "20251110", out)
    limit = fg.generate_limit_up_fixture(exports, "20251110", out)

    assert susp == 2
    assert st == 1
    # 000001.SZ 在 DAILY_DF 中 close == high（11.63），视为涨停
    assert limit == 1

    payload = json.loads((out / "suspended_000407_SZ_20251110.json").read_text())
    assert payload["_source"] == "pandadata_suspended"
    assert payload["_date"] == "20251110"
    assert payload["suspended_codes"] == {"000407.SZ": True}

    st_payload = json.loads((out / "st_000004_SZ_20251110.json").read_text())
    assert st_payload["code"] == "ST000004.SZ"
    assert st_payload["_real_code"] == "000004.SZ"

    limit_payload = json.loads((out / "limit_up_000001_SZ_20251110.json").read_text())
    assert limit_payload["_source"] == "pandadata_limit_up"
    assert limit_payload["price"] == pytest.approx(11.63)


def test_fixture_generator_rejects_truncated_export(tmp_path):
    from scripts import fixture_generator as fg

    truncated = dataframe(
        ["symbol", "date", "is_suspended"],
        [["000407.SZ", "20251110", True]],
        truncated=True,
        reasons=["byte_limit"],
    )
    out = tmp_path / "generated"
    out.mkdir()
    # 截断数据不落盘，且不抛异常打断手工夹具生成
    assert fg.generate_suspended_fixture({"suspended": [truncated]}, "20251110", out) == 0
    assert list(out.glob("suspended_*.json")) == []


def test_fixture_generator_skips_symbols_outside_trade_list(tmp_path):
    from scripts import fixture_generator as fg

    daily = dataframe(
        ["date", "symbol", "open", "high", "low", "close"],
        [["20251110", "600000.SH", 1.0, 2.0, 1.0, 2.0]],
    )
    out = tmp_path / "generated"
    out.mkdir()
    exports = {"trade_list": [TRADE_LIST], "stock_daily": [daily]}
    assert fg.generate_limit_up_fixture(exports, "20251110", out) == 0


def test_fixture_generator_main_from_mcp_end_to_end(tmp_path, monkeypatch):
    from scripts import fixture_generator as fg

    src = tmp_path / "mcp_exports"
    src.mkdir()
    _write_json(src / "suspended.json", envelope("get_suspended_list", SUSPENDED_DF))
    _write_json(src / "st.json", envelope("get_st_list", ST_DF))
    out = tmp_path / "generated"

    monkeypatch.setattr(
        sys,
        "argv",
        ["fixture_generator.py", "--from-mcp", str(src), "--output-dir", str(out), "--date", "20251110"],
    )
    assert fg.main() == 0
    assert (out / "suspended_000407_SZ_20251110.json").exists()
    assert (out / "st_000004_SZ_20251110.json").exists()
    # 手工夹具仍然生成
    assert (out / "t_plus_1_15.json").exists()


def test_fixture_generator_main_returns_nonzero_on_bad_export(tmp_path, monkeypatch):
    from scripts import fixture_generator as fg

    src = tmp_path / "mcp_exports"
    src.mkdir()
    _write_json(src / "suspended.json", envelope("get_suspended_list", None, ok=False))
    out = tmp_path / "generated"

    monkeypatch.setattr(
        sys,
        "argv",
        ["fixture_generator.py", "--from-mcp", str(src), "--output-dir", str(out)],
    )
    assert fg.main() == 1
