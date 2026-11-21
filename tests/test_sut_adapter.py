"""被测系统（SUT）不可用时必须失败，不能用 hold 冒充。

背景：`scripts/sut_adapter.py` 的 `from build import run` 在「被测系统目录不存在、
但本包 `scripts/` 已在 `sys.path` 上」时，会静默导入**本包自己的** `scripts/build.py`。
那个 `run()` 返回 dict 而不是 list，随后被 `except Exception` 吞成逐条 hold，
于是报告显示 `baseline_order_events=0`、违规 0、拦截 0、`inconclusive` ——
读起来像「被测系统很守规」，实际是「被测系统一次都没跑」，而 `config_hash`
保持不变，外部无法从哈希看出差别。这是最坏的一种失败，必须有测试把守。
"""

from __future__ import annotations

import os
import sys

import pytest


def _reload_adapter(scripts_dir: str):
    """重新导入 adapter，让它按给定的 CONSTRAINT_SUT_SCRIPTS 重新解析。"""
    import importlib

    os.environ["CONSTRAINT_SUT_SCRIPTS"] = scripts_dir
    import scripts.sut_adapter as adapter

    return importlib.reload(adapter)


def test_missing_sut_directory_raises_instead_of_returning_hold(tmp_path):
    adapter = _reload_adapter(str(tmp_path / "does_not_exist"))
    with pytest.raises(adapter.SutUnavailable) as exc:
        adapter.resolve_sut()
    assert "不存在" in exc.value.message
    assert exc.value.hint


def test_impostor_build_py_from_this_package_is_rejected(tmp_path, monkeypatch):
    """本包自己的 scripts/build.py 顶替被测系统时，必须报错而不是静默采用。

    这正是评审复现的路径：目录为空，`from build import run` 命中本包
    `scripts/build.py`，签名不符但异常被吞，报告退化成全 hold。
    """
    sut_scripts = tmp_path / "sut" / "scripts"
    sut_scripts.mkdir(parents=True)

    package_scripts = pathlib_scripts_dir()
    monkeypatch.syspath_prepend(str(package_scripts))
    monkeypatch.delitem(sys.modules, "build", raising=False)

    adapter = _reload_adapter(str(sut_scripts))
    with pytest.raises(adapter.SutUnavailable) as exc:
        adapter.resolve_sut()
    message = exc.value.message
    assert "不在被测系统目录" in message
    assert "build.py" in message


def test_a_genuine_sut_in_its_own_tree_resolves(tmp_path, monkeypatch):
    """真被测系统（run 定义在它自己目录里）必须能通过，证明校验不是一律拒绝。"""
    sut_scripts = tmp_path / "sut" / "scripts"
    sut_scripts.mkdir(parents=True)
    (sut_scripts / "build.py").write_text(
        "def run(input_data, config=None):\n"
        "    return [{'code': input_data['code'], 'action': 'hold',\n"
        "             'qty_change': 0, 'target_qty': 0, 'current_qty': 0,\n"
        "             'pnl_pct': 0, 'time': '10:30', 'reason': 'stub'}]\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(pathlib_scripts_dir()))
    monkeypatch.delitem(sys.modules, "build", raising=False)

    adapter = _reload_adapter(str(sut_scripts))
    sut_run = adapter.resolve_sut()
    assert isinstance(sut_run({"code": "X"}, None), list)


def pathlib_scripts_dir():
    """本包 ``scripts/`` 的绝对路径。"""
    from pathlib import Path

    return Path(__file__).resolve().parents[1] / "scripts"