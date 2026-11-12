# -*- coding: utf-8 -*-
"""CLI cmd_render 回归：pipeline 返回 success=False 时必须干净退出，
而不是 KeyError: 'elapsed_s'。"""
import types
from argparse import Namespace

import pytest

from cli import omotion as cli


class _FakeInfo:
    name = "fake"
    output_width = 1920
    output_height = 1080
    duration_seconds = 5.0


class _FailingPipeline:
    def __init__(self, *a, **kw):
        pass

    def render(self, *a, **kw):
        return {
            "success": False,
            "output_path": "out.mp4",
            "format": "personal-download-v1",
            "error_stage": "plan",
            "error": "参数验证失败(v2)",
            "error_detail": "INVALID_RUNTIME_ARGUMENTS",
        }


class _OkPipeline:
    def __init__(self, *a, **kw):
        pass

    def render(self, *a, **kw):
        return {"success": True, "elapsed_s": 12.3, "verified": True}


def _args(tmp_path):
    tpl = tmp_path / "t.motion"
    tpl.write_bytes(b"PK\x03\x04")
    params = tmp_path / "p.json"
    params.write_text("{}", encoding="utf-8")
    return Namespace(
        template=str(tpl), output=str(tmp_path / "out.mp4"),
        params=str(params), skip_doctor=True, preview=False,
    )


def test_render_failure_path_exits_1(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "RenderPipeline", _FailingPipeline)
    monkeypatch.setattr(cli, "detect_format", lambda p: "personal-download-v1")
    monkeypatch.setattr(cli, "parse_template", lambda p: _FakeInfo())
    monkeypatch.setattr(cli, "ParamSchema", types.SimpleNamespace(
        from_template=staticmethod(lambda p: None)))

    with pytest.raises(SystemExit) as exc:
        cli.cmd_render(_args(tmp_path))
    assert exc.value.code == 1


def test_render_success_path_no_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "RenderPipeline", _OkPipeline)
    monkeypatch.setattr(cli, "detect_format", lambda p: "standard")
    monkeypatch.setattr(cli, "parse_template", lambda p: _FakeInfo())
    monkeypatch.setattr(cli, "ParamSchema", types.SimpleNamespace(
        from_template=staticmethod(lambda p: None)))

    class _Report:
        status = types.SimpleNamespace(value="pass")
        summary = {"passed": 1, "warnings": 0, "failed": 0}
        checks = []

    monkeypatch.setattr(cli, "QualityGate", lambda: types.SimpleNamespace(
        check=lambda *a, **kw: _Report()))

    cli.cmd_render(_args(tmp_path))  # 不应抛异常
