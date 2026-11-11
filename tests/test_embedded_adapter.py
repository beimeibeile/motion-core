# -*- coding: utf-8 -*-
"""Tests for the embedded-adapter engine and its contract detection."""
import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from core.renderer.embedded_adapter import (
    EmbeddedAdapterEngine,
    detect_contract,
    has_embedded_adapter,
)
from core.renderer import embedded_adapter as ea

POS_ADAPTER = """
import argparse
p = argparse.ArgumentParser()
p.add_argument("op", choices=["plan", "render"])
p.parse_args()
"""

FLAGS_ADAPTER = """
import argparse
p = argparse.ArgumentParser()
p.add_argument("--inputs", required=True)
p.add_argument("--out", required=True)
p.add_argument("op", choices=["plan", "render"])
p.parse_args()
"""


def _bundle(tmp_path: Path, name: str, content: str) -> Path:
    d = tmp_path / name
    (d / "src").mkdir(parents=True)
    (d / "src" / "adapter.py").write_text(content, encoding="utf-8")
    return d


def test_detect_py_positional(tmp_path):
    d = _bundle(tmp_path, "b1", POS_ADAPTER)
    profile, adapter = detect_contract(d)
    assert profile == ea.PY_POSITIONAL
    assert adapter == d / "src" / "adapter.py"


def test_detect_py_op_flags(tmp_path):
    d = _bundle(tmp_path, "b2", FLAGS_ADAPTER)
    profile, _ = detect_contract(d)
    assert profile == ea.PY_OP_FLAGS


def test_detect_mjs_instance(tmp_path):
    d = tmp_path / "b3"
    (d / "src").mkdir(parents=True)
    (d / "src" / "adapter.mjs").write_text(
        "const args = process.argv; if(!options.instance)throw Error('需要 --instance');",
        encoding="utf-8",
    )
    profile, adapter = detect_contract(d)
    assert profile == ea.MJS_INSTANCE
    assert adapter.suffix == ".mjs"


def test_detect_mjs_without_instance_fails(tmp_path):
    d = tmp_path / "b4"
    (d / "src").mkdir(parents=True)
    (d / "src" / "adapter.mjs").write_text("throw Error('unknown')", encoding="utf-8")
    from core.renderer.engine import RenderError
    with pytest.raises(RenderError):
        detect_contract(d)


def test_detect_no_adapter_fails(tmp_path):
    from core.renderer.engine import RenderError
    with pytest.raises(RenderError):
        detect_contract(tmp_path)


def _make_motion(path: Path, inner_entries: dict, release_is_zip: bool = True) -> None:
    if release_is_zip:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n, data in inner_entries.items():
                z.writestr(n, data)
        release = buf.getvalue()
    else:
        release = b"not a zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("motion.json", "{}")
        z.writestr("content/release.bin", release)


def test_has_embedded_adapter_true(tmp_path):
    p = tmp_path / "t.motion"
    _make_motion(p, {"src/adapter.py": "print(1)", "guide.json": "{}"})
    assert has_embedded_adapter(str(p)) is True


def test_has_embedded_adapter_false(tmp_path):
    p = tmp_path / "t.motion"
    _make_motion(p, {"src/other.py": "x"})
    assert has_embedded_adapter(str(p)) is False
    p2 = tmp_path / "t2.motion"
    _make_motion(p2, {}, release_is_zip=False)
    assert has_embedded_adapter(str(p2)) is False


def _engine_with_bundle(tmp_path: Path, contract: str) -> EmbeddedAdapterEngine:
    instance = tmp_path / "instance"
    instance.mkdir()
    engine = EmbeddedAdapterEngine(str(tmp_path / "t.motion"), str(instance))
    bundle = instance / "bundle"
    (bundle / "src").mkdir(parents=True)
    if contract == ea.MJS_INSTANCE:
        adapter = bundle / "src" / "adapter.mjs"
        adapter.write_text("需要 --instance", encoding="utf-8")
    else:
        adapter = bundle / "src" / "adapter.py"
        adapter.write_text("pass", encoding="utf-8")
    engine.bundle_dir = bundle
    # 契约检测本身由上面的 detect_* 用例覆盖；这里直接指定以隔离命令链测试
    engine.contract = contract
    engine.adapter = adapter
    return engine


@pytest.mark.parametrize("contract,produced", [
    (ea.PY_POSITIONAL, "embed/video.mp4"),
    (ea.PY_OP_FLAGS, "render_out/video.mp4"),
    (ea.MJS_INSTANCE, "embed/video.mp4"),
])
def test_render_command_chain(tmp_path, monkeypatch, contract, produced):
    monkeypatch.setenv("PLAYWRIGHT_MODULE", str(tmp_path / "fake-pw"))
    engine = _engine_with_bundle(tmp_path, contract)
    recorded = []

    def fake_run(argv, cwd, timeout=600):
        recorded.append(argv)
        if (contract == ea.PY_POSITIONAL and argv[2] == "render") or \
           (contract == ea.PY_OP_FLAGS and "render" in argv) or \
           (contract == ea.MJS_INSTANCE and "render" in argv):
            target = engine.instance_dir / produced
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"fake-mp4")
        return 0, "", ""

    monkeypatch.setattr(engine, "_run", fake_run)
    out = tmp_path / "out" / "final.mp4"
    result = engine.render({"title": "x"}, str(out))
    assert result["success"] is True
    assert result["contract"] == contract
    assert out.read_bytes() == b"fake-mp4"
    # every recorded stage is one of the declared subcommands
    for argv in recorded:
        assert argv[2] in {"plan", "prepare", "preview", "render"}
    stages = [argv[2] for argv in recorded]
    if contract == ea.MJS_INSTANCE:
        assert stages == ["plan", "prepare", "preview", "render"]
    else:
        assert stages == ["plan", "render"]
        # playwright module must be threaded through the render stage
        assert any(str(tmp_path / "fake-pw") in argv for argv in recorded)


def test_render_without_playwright_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("PLAYWRIGHT_MODULE", raising=False)
    monkeypatch.setattr(ea, "find_playwright_module", lambda explicit=None: None)
    engine = _engine_with_bundle(tmp_path, ea.PY_POSITIONAL)
    from core.renderer.engine import RenderError
    with pytest.raises(RenderError):
        engine.render({"title": "x"}, str(tmp_path / "out.mp4"))
