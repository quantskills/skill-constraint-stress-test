"""前置检查契约的反向守卫（对应 SKILL.md 同名小节）。

本技能运行在 QuantStudio 环境：数据、被测模型与解释器都由宿主提供，脚本只读本地夹具。
所以 SKILL.md 必须先要求一次前置检查，且不得再出现任何「技能自己准备数据」的符号。
少了这条约束，收件人会直接跳进评测：把桩件跑出来的 hold 当成「AI 交易员守住了约束」，
或者把三年前落盘的夹具当成当日行情报违反率——指标照样好看，结论已经失真。
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL = REPO_ROOT / "SKILL.md"
TEXT = SKILL.read_text(encoding="utf-8")

REQUIRED = ("QuantStudio", "auth_status", "reauth_required",
            "data_permission_required", "import pytest")

DOC_FILES = ("SKILL.md", "README.md", "README.en.md", "INSTALL.md")
DOC_DIRS = ("references", "agents")


def _preflight_block() -> tuple[int, str]:
    i = TEXT.find("## 前置检查")
    assert i >= 0, "SKILL.md 必须有前置检查小节"
    j = TEXT.find("\n## ", i + 5)
    return i, TEXT[i:j if j > 0 else len(TEXT)]


def test_preflight_section_exists_and_covers_host_capabilities():
    """小节必须真的列出各项前提，而不是只有一句「请先检查环境」。"""
    _, block = _preflight_block()
    for need in REQUIRED:
        assert need in block, f"前置检查缺少 {need}"
    assert "标准库" in block, "必须写明评测链路只用标准库"
    assert "宿主" in block, "必须写明运行环境、模型与数据由宿主（QuantStudio）提供"
    assert "停止" in block and ("缺" in block or "报告" in block), \
        "必须写明不过就停并报告缺什么"


def test_preflight_precedes_everything_executable():
    """前置检查必须排在所有可执行流程之前，否则等于没有。"""
    i, _ = _preflight_block()
    for later in ("python3 scripts/", "python scripts/", "```bash", "```python"):
        k = TEXT.find(later)
        assert k == -1 or k > i, f"{later} 出现在前置检查之前"


def test_preflight_splits_refetch_from_offline_evaluation():
    """两条线路的阻塞语义必须写清。

    一律阻塞会让无网络的冷克隆与 CI 跑不起来；一律放行会让收件人把未复核的
    旧夹具当成当日数据报出违反率。
    """
    _, block = _preflight_block()
    assert "阻塞项" in block, "必须写明生成/更新夹具时 MCP 是阻塞项"
    assert "不是阻塞项" in block, "必须写明离线评测时 MCP 不是阻塞项"
    assert "mcp_data_source" in block, "必须点明 env_check 该项恒为 ok 是设计如此"
    assert "截断" in block, "必须要求处理 truncated / row_count<total_rows"


def test_preflight_forbids_reading_stub_results_as_trader_behaviour():
    """桩件只能证明链路可跑；把它写成「AI 交易员守住约束」是本 skill 最贵的误读。"""
    _, block = _preflight_block()
    assert "stub" in block, "必须说明默认被测系统是桩件"
    assert "inconclusive" in block, "trader=stub 时必须判为 inconclusive"
    assert "温度" in block and "config_hash" in block, "必须要求固定温度与可比性字段"


def test_docs_never_describe_a_data_fetching_client():
    """文档里不得再出现取数组件（旧口径称 SDK）或凭证符号，取数只有 MCP 一条路。"""
    banned = ("SDK", "import panda" + "_data", "from panda" + "_data",
              "PANDA" + "_DATA_", "panda" + "-data", "init" + "_token")
    paths = [REPO_ROOT / rel for rel in DOC_FILES]
    for sub in DOC_DIRS:
        paths += sorted((REPO_ROOT / sub).glob("*"))
    hits = []
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in banned:
            if token in text:
                hits.append(f"{path.name}: {token}")
    assert hits == [], "文档仍描述取数组件/凭证通道：" + "；".join(hits)
