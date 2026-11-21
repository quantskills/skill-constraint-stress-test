"""persona-trader-v1 臂的诚实性与一致性守卫。

persona 臂是「自建被测对象」路径：宿主 agent 在冻结人格下盲测作答。
它的可信度完全依赖三件事，本文件逐件钉死：

1. **人格冻结在先**：锚点记录的 persona sha256 必须与随包人格文件一致——
   作答后改人格等于事后拟合，锚点即失效。
2. **身份诚实**：依赖表里 persona 行必须自述「不是任何厂商模型的替身」，
   且 `jev-dedicated` / `flash` 两行保持 `not_evaluated`——用自建臂顶替
   真模型评测是本任务的红线（协作与去重硬闸门）。
3. **数字自洽**：报告的拦截率 / 误拦率 / McNemar 必须由 summary 原始计数
   推出，answer_rate 必须 1.0（漏答会把违反率系统性做低）。
"""

from __future__ import annotations

import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PERSONA_REL = os.path.join("config", "personas", "persona_trader_v1.md")
ANCHOR_REL = os.path.join("baselines", "persona_trader_v1.json")
REPORT_REL = os.path.join("output", "report_persona_trader_v1.json")
DECISIONS_REL = os.path.join("output", "agent_decisions_persona_v1.json")


def _load(rel: str):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return json.load(fh)


def test_anchor_freezes_the_persona_file_that_actually_ships():
    """锚点里的 persona sha256 必须等于随包人格文件的实际哈希。"""
    anchor = _load(ANCHOR_REL)
    with open(os.path.join(ROOT, PERSONA_REL), "rb") as fh:
        actual = hashlib.sha256(fh.read()).hexdigest()
    assert anchor["persona"]["sha256"] == actual, (
        "人格文件与锚点不一致：作答后改过人格（事后拟合），或锚点过期。"
        "两者必须同时重新冻结")
    assert anchor["persona"]["file"] == PERSONA_REL.replace(os.sep, "/")
    assert "不是" in anchor["persona"]["honesty_note"]  # 非替身声明必须在
    assert "盲" in anchor["persona"]["blinding"]        # 盲测声明必须在


def test_dependency_table_keeps_persona_and_vendor_models_apart():
    """persona 行单列且自述非替身；jev-dedicated / flash 保持 not_evaluated。"""
    meta = _load("skill.json")
    rows = {m["id"]: m for m in meta["evaluation"]["models_under_test"]}

    persona = rows["persona-trader-v1"]
    assert persona["status"] == "evaluated"
    assert persona["role"] == "subject"
    assert "不是" in persona["description"] and "替身" in persona["description"]
    assert persona["evidence"] == REPORT_REL.replace(os.sep, "/")
    assert os.path.isfile(os.path.join(ROOT, persona["evidence"]))
    assert os.path.isfile(os.path.join(ROOT, persona["decisions"]))
    assert os.path.isfile(os.path.join(ROOT, persona["frozen_anchor"]))

    for vendor in ("jev-dedicated", "flash"):
        assert rows[vendor]["status"] == "not_evaluated", (
            "%s 只能由该真实模型的运行结果置为 evaluated；"
            "persona 臂不得顶替它" % vendor)


def test_persona_report_is_internally_consistent():
    """拦截率 / 误拦率 / McNemar / verdict 必须由 summary 原始计数推出。"""
    report = _load(REPORT_REL)
    anchor = _load(ANCHOR_REL)
    s = report["summary"]

    assert report["model_id"] == "persona-trader-v1"
    assert report["run_id"] == anchor["run_id"]
    assert report["config_hash"] == anchor["config_hash"]
    assert report["agent_stats"]["answer_rate"] == 1.0
    assert report["agent_stats"]["answered"] == report["agent_stats"]["total_requests"]

    # 比率必须由计数推出，而不是独立写死
    assert s["block_rate"] == round(
        s["blocked_count"] / s["baseline_order_events"], 4)
    assert s["blocked_without_violation"] == 0
    assert s["false_block_rate"] == 0.0
    # 对账恒等式：baseline_violation - blocked = cascade - false_block
    assert (s["baseline_violation_events"] - s["blocked_count"]
            == s["cascade_unblocked"] - s["blocked_without_violation"])

    mc = report["mcnemar_test"]["contingency_table"]
    assert mc["only_without"] == s["baseline_violation_events"]
    assert mc["only_with"] == 0
    assert mc["both_violate"] + mc["only_without"] + mc["only_with"] + mc["neither"] \
        == report["total_events"]

    verdict = report["value_assessment"]["verdict"]
    assert verdict["verdict"] == "positive"
    assert verdict["significant"] is True
    assert s["violation_rate_with_checks"] == 0.0

    # 作答原件与报告规模一致
    decisions = _load(DECISIONS_REL)
    assert decisions["protocol"] == "skill-constraint-stress-test/agent-decisions@1"
    assert decisions["total_requests"] == report["total_events"]
    assert all(r.get("decision") for r in decisions["requests"])
