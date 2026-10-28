# -*- coding: utf-8 -*-
"""Tests for core.template_parser (standard + personal)."""
import zipfile
from pathlib import Path

import pytest

from core.template_parser import detect_format, parse_template
from core.template_parser.standard import StandardParser
from core.template_parser.personal import PersonalParser
from core.template_parser.base import TemplateInfo

from conftest import make_standard_zip, make_personal_zip


# ── format detection ────────────────────────────────────────────────────

class TestDetectFormat:
    def test_standard(self, standard_tpl):
        assert detect_format(str(standard_tpl)) == "standard"

    def test_personal_v2(self, personal_tpl_v2):
        assert detect_format(str(personal_tpl_v2)) == "personal-download-v1"

    def test_invalid_file(self, tmp_path):
        bad = tmp_path / "bad.motion"
        bad.write_bytes(b"not a zip")
        with pytest.raises(Exception):
            detect_format(str(bad))

    def test_missing_file(self, tmp_path):
        with pytest.raises(Exception):
            detect_format(str(tmp_path / "nope.motion"))


# ── standard parser ────────────────────────────────────────────────────

class TestStandardParser:
    def test_parse_fields(self, standard_tpl):
        info = parse_template(str(standard_tpl))
        assert isinstance(info, TemplateInfo)
        assert info.format == "standard"
        assert info.template_id == "test-standard"
        assert info.name == "测试模板"
        assert info.output_width == 1920
        assert info.output_height == 1080
        assert info.output_fps == 60.0
        assert info.runtime_profile == "standard"
        assert info.duration_seconds == 5.0
        assert info.parameters_schema is not None
        assert "scenes" in info.parameters_schema["properties"]
        assert any("core/assets/" in a for a in info.assets)
        assert any("licenses/" in l for l in info.licenses)

    def test_parse_fps_dict(self, tmp_path):
        import json
        tpl2 = tmp_path / "fps_dict2.motion"
        make_standard_zip(tpl2)
        with zipfile.ZipFile(tpl2) as z:
            names = z.namelist()
            items = {n: z.read(n) for n in names}
        motion = json.loads(items["motion.json"])
        motion["output"]["fps"] = {"num": 30, "den": 1}
        items["motion.json"] = json.dumps(motion, ensure_ascii=False).encode()
        with zipfile.ZipFile(tpl2, "w") as z:
            for n, data in items.items():
                z.writestr(n, data)
        info = parse_template(str(tpl2))
        assert info.output_fps == 30.0

    def test_validate_ok(self, standard_tpl):
        assert StandardParser(str(standard_tpl)).validate() == []

    def test_validate_missing_motion(self, tmp_path):
        tpl = make_standard_zip(tmp_path / "no_motion.motion", missing=["motion.json"])
        errors = StandardParser(str(tpl)).validate()
        assert "missing motion.json" in errors

    def test_validate_missing_schema(self, tmp_path):
        tpl = make_standard_zip(tmp_path / "no_schema.motion",
                                missing=["params.schema.json"])
        errors = StandardParser(str(tpl)).validate()
        assert "missing params.schema.json" in errors

    def test_validate_bad_zip(self, tmp_path):
        bad = tmp_path / "bad.motion"
        bad.write_bytes(b"garbage")
        errors = StandardParser(str(bad)).validate()
        assert any("not a valid ZIP" in e for e in errors)

    def test_parse_missing_motion_raises(self, tmp_path):
        tpl = make_standard_zip(tmp_path / "no_motion.motion", missing=["motion.json"])
        with pytest.raises(ValueError):
            StandardParser(str(tpl)).parse()


# ── personal parser ────────────────────────────────────────────────────

class TestPersonalParser:
    def test_parse_v2(self, personal_tpl_v2):
        info = parse_template(str(personal_tpl_v2))
        assert info.format == "personal-download-v1"
        assert info.package_profile == "personal-download-v1"
        assert info.runtime_generation == "v2"
        assert info.has_encrypted_content is True
        assert info.parameters_schema is None  # encrypted, not extracted
        assert info.output_width == 1920  # from assistant/guide.json
        assert info.services == {"tts": True}

    def test_parse_v1(self, personal_tpl_v1):
        info = parse_template(str(personal_tpl_v1))
        assert info.runtime_generation == "v1"

    def test_validate_ok(self, personal_tpl_v2):
        assert PersonalParser(str(personal_tpl_v2)).validate() == []

    def test_validate_missing_key(self, tmp_path):
        tpl = make_personal_zip(tmp_path / "no_key.motion")
        with zipfile.ZipFile(tpl) as z:
            names = z.namelist()
            items = {n: z.read(n) for n in names if n != "personal/motiongit.key.json"}
        with zipfile.ZipFile(tpl, "w") as z:
            for n, data in items.items():
                z.writestr(n, data)
        errors = PersonalParser(str(tpl)).validate()
        assert "missing personal/motiongit.key.json" in errors

    def test_parse_missing_motion_raises(self, tmp_path):
        tpl = make_personal_zip(tmp_path / "no_motion.motion")
        with zipfile.ZipFile(tpl) as z:
            names = z.namelist()
            items = {n: z.read(n) for n in names if n != "motion.json"}
        with zipfile.ZipFile(tpl, "w") as z:
            for n, data in items.items():
                z.writestr(n, data)
        with pytest.raises(ValueError):
            PersonalParser(str(tpl)).parse()
