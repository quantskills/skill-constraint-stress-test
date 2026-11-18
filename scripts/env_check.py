"""运行环境自检 — 跑主流程前先回答"能不能干"。"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class CheckItem:
    name: str
    status: str  # "ok" | "warn" | "blocked"
    found: str = ""
    expected: str = ""
    hint: str = ""


@dataclass
class CheckGroup:
    name: str
    items: list[CheckItem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(item.status != "blocked" for item in self.items)


def _check_python_version() -> CheckItem:
    v = sys.version_info
    version_str = f"{v.major}.{v.minor}.{v.micro}"
    if v >= (3, 10):
        return CheckItem("python_version", "ok", found=version_str, expected=">=3.10")
    return CheckItem(
        "python_version",
        "blocked",
        found=version_str,
        expected=">=3.10",
        hint="Upgrade Python to 3.10+",
    )


def _check_pyyaml() -> CheckItem:
    try:
        import yaml  # noqa: F401

        return CheckItem("pyyaml", "ok", found="installed")
    except ImportError:
        return CheckItem(
            "pyyaml",
            "warn",
            found="not installed",
            expected="optional",
            hint="pip install pyyaml  (graceful degradation to built-in defaults)",
        )


def _check_mcp_source_note() -> CheckItem:
    """运行与取数都在 QuantStudio 环境：数据由 agent 通过 PandaData MCP 取回落盘，本 skill 执行阶段只读本地 fixture。

    这里刻意**不联网、不探测 MCP**：离线评测不需要 MCP，缺失也不构成阻塞；
    需要重新取数时由 SKILL.md 的「前置检查」拦下。
    """
    return CheckItem(
        "mcp_data_source",
        "ok",
        found="QuantStudio host: agent-side MCP (mcp__pandadata__call_pandadata)",
        expected="agent acquires data; skill reads local fixtures only",
        hint="Data comes from MCP tool calls made by the reading agent; this skill never calls the network.",
    )


def _check_fixtures_dir() -> CheckItem:
    fixtures_dir = REPO_ROOT / "fixtures"
    if fixtures_dir.is_dir():
        return CheckItem("fixtures_dir", "ok", found=str(fixtures_dir))
    return CheckItem(
        "fixtures_dir",
        "blocked",
        found="not found",
        expected=str(fixtures_dir),
        hint="Create fixtures/ directory with test cases",
    )


def _check_config_file() -> CheckItem:
    config_file = REPO_ROOT / "config" / "eval_config.yaml"
    if config_file.exists():
        return CheckItem("config_file", "ok", found=str(config_file))
    return CheckItem(
        "config_file",
        "warn",
        found="not found",
        expected=str(config_file),
        hint="Using built-in defaults; create config/eval_config.yaml for custom settings",
    )


def run_self_check() -> dict:
    """执行全部自检项，返回结构化报告。"""
    groups = [
        CheckGroup(
            "runtime",
            [_check_python_version()],
        ),
        CheckGroup(
            "dependencies",
            [_check_pyyaml()],
        ),
        CheckGroup(
            "data_access",
            [_check_mcp_source_note(), _check_fixtures_dir()],
        ),
        CheckGroup(
            "resources",
            [_check_config_file()],
        ),
    ]

    all_ok = all(g.ok for g in groups)
    report = {
        "ok": all_ok,
        "status": "ok" if all_ok else "blocked",
        "groups": [
            {
                "name": g.name,
                "ok": g.ok,
                "items": [
                    {
                        "name": item.name,
                        "status": item.status,
                        "found": item.found,
                        "expected": item.expected,
                        "hint": item.hint,
                    }
                    for item in g.items
                ],
            }
            for g in groups
        ],
    }
    return report


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="constraint-stress-test environment self-check")
    parser.add_argument("--self-check", action="store_true", help="Run self-check")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    if not args.self_check:
        parser.print_help()
        return 2

    report = run_self_check()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        status_icon = "✅" if report["ok"] else "❌"
        print(f"{status_icon} Status: {report['status']}")
        for group in report["groups"]:
            g_icon = "✅" if group["ok"] else "❌"
            print(f"\n  {g_icon} [{group['name']}]")
            for item in group["items"]:
                i_icon = {"ok": "✅", "warn": "⚠️", "blocked": "❌"}[item["status"]]
                print(f"    {i_icon} {item['name']}: {item['found']}")
                if item["hint"]:
                    print(f"       → {item['hint']}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
