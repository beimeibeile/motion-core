"""渲染前置 doctor 自检（配置指令 §4.4）：判定逻辑 + CLI 接线。

背景：全库冒烟实测「preview/render 退出码非 0 但产物有效」，而真正的环境缺失
（node / ffmpeg / ffprobe / Chrome）会让渲染在几十帧后才崩、错误信息也不指向根因。
因此 render 必须先过 doctor：missing 直接中止，warn 只提示，`--skip-doctor` 可显式绕过并留痕。
"""

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from core.doctor import DoctorItem, DoctorReport, render_preflight

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_PATH = REPO_ROOT / "cli" / "omotion.py"


def _report(*items):
    report = DoctorReport()
    report.items = list(items)
    return report


def _all_ok():
    return _report(
        DoctorItem("node", "ok", "v22.23.2"),
        DoctorItem("ffmpeg", "ok", "ffmpeg version 7.x"),
        DoctorItem("ffprobe", "ok", "ffprobe version 7.x"),
        DoctorItem("chrome", "ok", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    )


def _load_cli():
    spec = importlib.util.spec_from_file_location("omotion_cli_under_test", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preflight_all_ok(monkeypatch):
    monkeypatch.setattr("core.doctor.environment.run_doctor",
                        lambda *a, **k: _all_ok())
    allowed, lines = render_preflight()
    assert allowed is True
    assert any("前置自检通过" in line for line in lines)


def test_preflight_missing_blocks_render(monkeypatch):
    monkeypatch.setattr("core.doctor.environment.run_doctor", lambda *a, **k: _report(
        DoctorItem("node", "ok", "v22.23.2"),
        DoctorItem("ffmpeg", "ok", "ffmpeg version 7.x"),
        DoctorItem("ffprobe", "ok", "ffprobe version 7.x"),
        DoctorItem("chrome", "missing", "未找到 Chrome（无头渲染依赖）",
                   command="chrome.exe 常见路径探测"),
    ))
    allowed, lines = render_preflight()
    assert allowed is False
    assert any("chrome" in line for line in lines)
    assert any("--skip-doctor" in line for line in lines)


def test_preflight_warn_does_not_block(monkeypatch):
    monkeypatch.setattr("core.doctor.environment.run_doctor", lambda *a, **k: _report(
        DoctorItem("node", "ok", "v22.23.2"),
        DoctorItem("ffmpeg", "ok", "ffmpeg version 7.x"),
        DoctorItem("ffprobe", "warn", "未找到 ffprobe（元数据探测降级）"),
        DoctorItem("chrome", "warn", "playwright 缓存存在"),
    ))
    allowed, lines = render_preflight()
    assert allowed is True
    assert any("ffprobe" in line for line in lines)


def test_preflight_skip_bypasses_doctor_entirely(monkeypatch):
    calls = []

    def _boom(*a, **k):
        calls.append(1)
        return _report(DoctorItem("node", "missing", "未找到 node"))

    monkeypatch.setattr("core.doctor.environment.run_doctor", _boom)
    allowed, lines = render_preflight(skip=True)
    assert allowed is True
    assert calls == []          # skip 时不探测，也不浪费时间
    assert any("已跳过" in line for line in lines)


def test_preflight_against_real_environment():
    """真环境跑一次：只校验返回形状，不锁定具体依赖有无。"""
    allowed, lines = render_preflight()
    assert isinstance(allowed, bool)
    assert isinstance(lines, list) and lines


def test_cli_render_exposes_skip_doctor_flag():
    proc = subprocess.run([sys.executable, str(CLI_PATH), "render", "--help"],
                          capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0
    assert "--skip-doctor" in proc.stdout


def test_cmd_render_aborts_before_any_rendering(monkeypatch):
    cli = _load_cli()
    monkeypatch.setattr("core.doctor.render_preflight",
                        lambda skip=False, template_dir=None:
                        (False, ["❌ doctor 前置自检未通过，渲染已中止"]))
    args = argparse.Namespace(template="no-such-template.motion", params=None,
                              output=None, preview=False, skip_doctor=False)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_render(args)
    assert exc.value.code == 1


def test_cmd_render_tolerates_absent_skip_attr(monkeypatch, capsys):
    """老调用方构造的 Namespace 可能没有 skip_doctor，不能因此崩。"""
    cli = _load_cli()
    monkeypatch.setattr("core.doctor.render_preflight",
                        lambda skip=False, template_dir=None: (True, []))
    args = argparse.Namespace(template="no-such-template.motion", params=None,
                              output=None, preview=False)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_render(args)
    assert exc.value.code == 1
    assert "必须提供 --params" in capsys.readouterr().err