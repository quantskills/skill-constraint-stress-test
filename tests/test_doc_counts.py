"""cst 0.5.0：文档计数守卫 + 把三处失真数字改回实测。

并发会话在 0.3.x–0.4.2 做了 5 刀重构（删代理 LLM 通道、补统计口径与可比性用例），
代码和用例都变了，但 `README` / `data_access` / `validation_evidence` 里的用例数还停在
219，逐文件表里三行也停在旧分布。这类失真没有任何测试会报——而 219 恰恰是审核方
照着复跑的第一批数字。补一条守卫，把"文档 == 实测"钉成断言。
"""
from __future__ import annotations

import pathlib
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _collection() -> dict:
    """用 pytest 自己的收集结果作为唯一真相（不数 `def test_`，参数化会让两者不等）。"""
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    counts: dict = {}
    for line in proc.stdout.splitlines():
        if line.startswith("tests/"):
            name = line.split("::", 1)[0].split("/", 1)[1]
            counts[name] = counts.get(name, 0) + 1
    return counts


@pytest.fixture(scope="module")
def collection() -> dict:
    return _collection()


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_readme_suite_totals_match_collection(collection):
    """两份 README 的「N 例全绿 / N passing」必须等于实收集数。"""
    import re

    total = sum(collection.values())
    patterns = (("README.md", r"pytest tests/ -v` — \*\*(\d+) 例全绿\*\*"),
                ("README.en.md", r"pytest tests/ -v` — \*\*(\d+) passing\*\*"))
    for rel, pattern in patterns:
        match = re.search(pattern, _read(rel))
        assert match, f"{rel} 不再声明套件总数"
        assert int(match.group(1)) == total, f"{rel} 写 {match.group(1)}，实收集 {total}"


def test_ci_count_in_data_access_matches_collection(collection):
    import re

    total = sum(collection.values())
    text = _read("references/data_access.md")
    shown = re.findall(r"(\d+) 个测试在 1 秒内跑完", text)
    assert shown, "data_access.md 不再给出用例总数"
    assert all(int(n) == total for n in shown), f"data_access 写 {shown}，实收集 {total}"


def test_evidence_head_totals_match_collection(collection):
    import re

    total = sum(collection.values())
    text = _read("references/validation_evidence.md")
    head = dict(re.findall(r"^\| (用例总数|通过|失败) \| (\d+) \|\s*$", text, re.M))
    assert head, "证据文档没有计数行？"
    assert int(head["用例总数"]) == total, (head["用例总数"], total)
    assert int(head["通过"]) == total, (head["通过"], total)
    assert int(head["失败"]) == 0, head["失败"]


def test_evidence_per_file_table_matches_collection(collection):
    """逐文件表必须与实收集**逐行**一致：这是 0.3.x 重构后失真最集中的地方。"""
    import re

    text = _read("references/validation_evidence.md")
    claimed = {m.group(1): int(m.group(2))
               for m in re.finditer(r"\| `(test_[a-z0-9_]+\.py)` \| (\d+) \|", text)}
    assert claimed, "证据文档没有逐文件用例表？"
    missing = sorted(set(collection) - set(claimed))
    extra = sorted(set(claimed) - set(collection))
    stale = {k: (claimed[k], collection[k])
             for k in set(claimed) & set(collection) if claimed[k] != collection[k]}
    assert not (missing or extra or stale), (
        f"漏列={missing} 多列={extra} 数字过期={stale}")
    assert sum(claimed.values()) == sum(collection.values())
