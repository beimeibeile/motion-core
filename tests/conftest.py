# -*- coding: utf-8 -*-
"""Shared fixtures for motion-core test suite.

Builds minimal-but-real .motion templates on the fly:
  - standard: plain ZIP (motion.json + params.schema.json + adapter.py +
    render.mjs + core/)
  - personal-download-v1: encrypted-style ZIP (motion.json + content/release.bin
    + personal/*.json + assistant/motion.mjs)
"""
import json
import io
import zipfile
from pathlib import Path

import pytest


def make_standard_zip(path: Path, *, scenes_max: int = 30,
                      missing: list | None = None) -> Path:
    """Create a minimal valid standard .motion ZIP."""
    missing = missing or []
    motion = {
        "specVersion": "0.1.0-draft.1",
        "id": "test-standard",
        "name": "测试模板",
        "template": {"id": "test-standard", "name": "测试模板", "version": "0.1.0"},
        "runtime": {"profile": "standard", "entry": "adapter.py"},
        "output": {"width": 1920, "height": 1080, "fps": 60,
                   "durationPolicy": "user-defined", "durationSeconds": 5},
        "mode": "standard", "language": "zh", "layout": "landscape",
    }
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["mode", "language", "layout", "scenes"],
        "properties": {
            "mode": {"type": "string", "const": "standard"},
            "language": {"type": "string", "enum": ["zh", "en"]},
            "layout": {"type": "string", "enum": ["landscape", "portrait"]},
            "title": {"type": "string", "minLength": 1, "maxLength": 40,
                      "default": "Hello"},
            "duration": {"type": "number", "minimum": 1.0, "maximum": 120.0,
                         "default": 5.0},
            "scenes": {"type": "array", "minItems": 1, "maxItems": scenes_max,
                       "items": {"type": "object",
                                 "required": ["id", "title"],
                                 "properties": {
                                     "id": {"type": "string"},
                                     "title": {"type": "string"},
                                 }}},
        },
        "additionalProperties": False,
    }
    guide = {"template": {"id": "test-standard", "name": "测试模板"}}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if "motion.json" not in missing:
            z.writestr("motion.json", json.dumps(motion, ensure_ascii=False))
        if "params.schema.json" not in missing:
            z.writestr("params.schema.json", json.dumps(schema, ensure_ascii=False))
        if "guide.json" not in missing:
            z.writestr("guide.json", json.dumps(guide, ensure_ascii=False))
        if "adapter.py" not in missing:
            z.writestr("adapter.py", "def plan(): pass\n")
        if "render.mjs" not in missing:
            z.writestr("render.mjs", "export default {}\n")
        z.writestr("core/assets/logo.png", b"\x89PNG\x0d\x0a\x1a\x0a")
        z.writestr("licenses/LICENSE.txt", "MIT\n")
    path.write_bytes(buf.getvalue())
    return path


def make_personal_zip(path: Path, *, runtime: str = "v2") -> Path:
    """Create a minimal personal-download-v1 .motion ZIP.

    runtime: 'v1' (motion-kit) or 'v2' (split_root) marker in motion.mjs.
    """
    motion = {
        "specVersion": "1.0.0-rc.1",
        "id": "test-personal",
        "name": "测试个人模板",
        "packageProfile": "personal-download-v1",
        "runtime": {"profile": "motiongit-host/1", "operation": "motion.run"},
        "services": {"tts": True},
    }
    if runtime == "v2":
        mjs = "// runtime helper\nconst SPLIT_ROOT = true;\n// canvas_split_root\n"
    else:
        mjs = "// runtime helper motion-kit host\nconst WORKFLOW = 'motion-kit';\n"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("motion.json", json.dumps(motion, ensure_ascii=False))
        z.writestr("content/release.bin", b"\x00" * 64)  # encrypted payload
        z.writestr("personal/motiongit.key.json", json.dumps({"key": "test-key"}))
        z.writestr("personal/receipt.json", json.dumps({"ok": True}))
        z.writestr("personal/signature.json", json.dumps({"sig": "x"}))
        z.writestr("assistant/motion.mjs", mjs)
        z.writestr("assistant/guide.json",
                   json.dumps({"template": {"id": "test-personal", "version": "2.7.10",
                                            "width": 1920, "height": 1080}}))
    path.write_bytes(buf.getvalue())
    return path


def standard_params() -> dict:
    """Valid params for the conftest standard template."""
    return {
        "mode": "standard",
        "language": "zh",
        "layout": "landscape",
        "title": "Hello",
        "duration": 5.0,
        "scenes": [{"id": "S01", "title": "场景 1"}],
    }


@pytest.fixture
def standard_tpl(tmp_path: Path) -> Path:
    return make_standard_zip(tmp_path / "test_standard.motion")


@pytest.fixture
def personal_tpl_v2(tmp_path: Path) -> Path:
    return make_personal_zip(tmp_path / "test_personal_v2.motion", runtime="v2")


@pytest.fixture
def personal_tpl_v1(tmp_path: Path) -> Path:
    return make_personal_zip(tmp_path / "test_personal_v1.motion", runtime="v1")


@pytest.fixture
def schema() -> dict:
    """Reusable JSON schema for param tests."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["mode", "count", "label"],
        "properties": {
            "mode": {"type": "string", "const": "standard"},
            "count": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
            "ratio": {"type": "number", "minimum": 0.5, "maximum": 2.0, "default": 1.0},
            "label": {"type": "string", "minLength": 2, "maxLength": 8},
            "tags": {"type": "array", "minItems": 1, "maxItems": 3,
                     "items": {"type": "string"}},
            "flag": {"type": "boolean", "default": False},
            "style": {"type": "string", "enum": ["a", "b", "c"]},
            "extra": {"type": "string"},
        },
        "additionalProperties": False,
    }
