# -*- coding: utf-8 -*-
"""Tests for core.quality_gate (four layers + 18/18 equivalent report)."""
import json
from pathlib import Path

import pytest

from core.quality_gate.gate import (
    QualityGate, QualityReport, CheckResult, CheckStatus, _ffprobe,
)
from conftest import standard_params


class TestQualityReport:
    def test_summary(self):
        r = QualityReport(task_id="t1", template="x.motion")
        r.checks = [
            CheckResult("static", "a", CheckStatus.PASS),
            CheckResult("params", "b", CheckStatus.PASS),
            CheckResult("render", "c", CheckStatus.WARN),
            CheckResult("output", "d", CheckStatus.FAIL),
        ]
        r.status = CheckStatus.FAIL  # gate.check 才会自动重算；直接构造需显式
        assert r.summary == {"total": 4, "passed": 2, "warnings": 1, "failed": 1}
        assert r.status == CheckStatus.FAIL

    def test_to_dict(self):
        r = QualityReport(task_id="t1", template="x.motion")
        r.checks = [CheckResult("static", "a", CheckStatus.PASS, "ok")]
        d = r.to_dict()
        assert d["status"] == "pass"
        assert d["summary"]["total"] == 1
        assert d["checks"][0]["stage"] == "static"


class TestStaticLayer:
    def test_file_missing(self, tmp_path):
        gate = QualityGate()
        results = gate.check_static(str(tmp_path / "nope.motion"))
        assert any(c.name == "file_exists" and c.status == CheckStatus.FAIL
                   for c in results)

    def test_not_zip(self, tmp_path):
        gate = QualityGate()
        f = tmp_path / "bad.motion"
        f.write_bytes(b"garbage")
        results = gate.check_static(str(f))
        assert any(c.name == "zip_valid" and c.status == CheckStatus.FAIL
                   for c in results)

    def test_standard_full(self, standard_tpl):
        results = QualityGate().check_static(str(standard_tpl))
        assert all(c.status == CheckStatus.PASS for c in results)
        assert {c.name for c in results} == {"file_exists", "zip_valid",
                                             "format_detect", "template_parse"}

    def test_personal_full(self, personal_tpl_v2):
        results = QualityGate().check_static(str(personal_tpl_v2))
        assert all(c.status == CheckStatus.PASS for c in results)


class TestParamsLayer:
    def test_non_dict(self, standard_tpl):
        results = QualityGate().check_params(str(standard_tpl), "bad")
        assert any(c.status == CheckStatus.FAIL for c in results)

    def test_empty_params(self, standard_tpl):
        results = QualityGate().check_params(str(standard_tpl), {})
        assert any(c.status == CheckStatus.FAIL for c in results)

    def test_standard_missing_required(self, standard_tpl):
        results = QualityGate().check_params(str(standard_tpl), {"mode": "standard"})
        names = {c.name for c in results if c.status == CheckStatus.FAIL}
        assert "required_language" in names
        assert "required_layout" in names
        assert "required_scenes" in names

    def test_standard_valid(self, standard_tpl):
        results = QualityGate().check_params(str(standard_tpl), standard_params())
        assert all(c.status == CheckStatus.PASS for c in results)

    def test_standard_scenes_over_30_warn(self, standard_tpl):
        params = standard_params()
        params["scenes"] = [{"id": f"S{i}", "title": f"t{i}"} for i in range(31)]
        results = QualityGate().check_params(str(standard_tpl), params)
        assert any(c.name == "scenes_limit" and c.status == CheckStatus.WARN
                   for c in results)

    def test_personal_missing_timeline(self, personal_tpl_v2):
        results = QualityGate().check_params(str(personal_tpl_v2), {"x": 1})
        assert any(c.name == "required_timeline" and c.status == CheckStatus.FAIL
                   for c in results)

    def test_personal_valid_timeline(self, personal_tpl_v2):
        results = QualityGate().check_params(
            str(personal_tpl_v2), {"timeline": {"segments": [{"id": "s1"}]}})
        assert all(c.status == CheckStatus.PASS for c in results)


class TestRenderLayer:
    def test_failure(self):
        results = QualityGate().check_render({"success": False, "error": "boom"})
        assert any(c.name == "render_success" and c.status == CheckStatus.FAIL
                   for c in results)

    def test_success_no_output(self, tmp_path):
        results = QualityGate().check_render(
            {"success": True, "output_path": str(tmp_path / "missing.mp4"),
             "elapsed_s": 3.2})
        assert any(c.name == "render_success" and c.status == CheckStatus.PASS
                   for c in results)
        assert any(c.name == "output_exists" and c.status == CheckStatus.FAIL
                   for c in results)

    def test_duration_sanity(self, tmp_path):
        out = tmp_path / "out.mp4"
        out.write_bytes(b"x")
        results = QualityGate().check_render(
            {"success": True, "output_path": str(out), "elapsed_s": 0})
        assert any(c.name == "duration_sanity" and c.status == CheckStatus.WARN
                   for c in results)


class TestOutputLayer:
    def test_file_missing(self, tmp_path):
        results = QualityGate().check_output(str(tmp_path / "nope.mp4"))
        assert any(c.status == CheckStatus.FAIL for c in results)

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.mp4"
        f.write_bytes(b"")
        results = QualityGate().check_output(str(f))
        assert any(c.name == "file_nonempty" and c.status == CheckStatus.FAIL
                   for c in results)

    def test_unreadable_ffprobe(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.quality_gate.gate._ffprobe",
                            lambda p: None)
        f = tmp_path / "fake.mp4"
        f.write_bytes(b"garbage")
        results = QualityGate().check_output(str(f))
        assert any(c.name == "decodable" and c.status == CheckStatus.FAIL
                   for c in results)

    def test_full_pass(self, tmp_path, monkeypatch):
        def fake_probe(p):
            return {
                "streams": [{"codec_type": "video", "codec_name": "h264",
                             "width": 1920, "height": 1080},
                            {"codec_type": "audio", "codec_name": "aac"}],
                "format": {"duration": "5.0", "size": "100"},
            }
        monkeypatch.setattr("core.quality_gate.gate._ffprobe", fake_probe)
        f = tmp_path / "ok.mp4"
        f.write_bytes(b"data")
        results = QualityGate().check_output(
            str(f), {"width": 1920, "height": 1080, "duration": 5.0,
                     "require_audio": True})
        assert all(c.status == CheckStatus.PASS for c in results)
        assert {c.name for c in results} == {
            "file_exists", "decodable", "video_stream", "resolution",
            "duration", "audio_stream"}

    def test_resolution_mismatch(self, tmp_path, monkeypatch):
        def fake_probe(p):
            return {"streams": [{"codec_type": "video", "codec_name": "h264",
                                 "width": 1280, "height": 720}],
                    "format": {"duration": "5.0"}}
        monkeypatch.setattr("core.quality_gate.gate._ffprobe", fake_probe)
        f = tmp_path / "res.mp4"
        f.write_bytes(b"data")
        results = QualityGate().check_output(
            str(f), {"width": 1920, "height": 1080})
        assert any(c.name == "resolution" and c.status == CheckStatus.FAIL
                   for c in results)


class TestCombinedGate:
    def test_fail_overall(self, standard_tpl):
        gate = QualityGate()
        report = gate.check(str(standard_tpl), {"mode": "standard"})
        assert report.status == CheckStatus.FAIL
        assert report.summary["failed"] >= 1

    def test_pass_overall_static_params(self, standard_tpl):
        gate = QualityGate()
        report = gate.check(str(standard_tpl), standard_params())
        # static(4) + params(6: params_type+4 required+scenes) = 10, all pass
        assert report.status == CheckStatus.PASS
        assert report.summary["passed"] == 10
        assert report.summary["failed"] == 0

    def test_full_18_18_equivalent(self, standard_tpl, tmp_path, monkeypatch):
        """Core regression: standard template full gate == 19/19 PASS
        (static 4 + params 6 + render 3 + output 6)."""
        def fake_probe(p):
            return {
                "streams": [{"codec_type": "video", "codec_name": "h264",
                             "width": 1920, "height": 1080},
                            {"codec_type": "audio", "codec_name": "aac"}],
                "format": {"duration": "5.0"},
            }
        monkeypatch.setattr("core.quality_gate.gate._ffprobe", fake_probe)
        out = tmp_path / "render.mp4"
        out.write_bytes(b"data")
        gate = QualityGate()
        report = gate.check(
            str(standard_tpl), standard_params(),
            output_path=str(out),
            render_result={"success": True, "output_path": str(out),
                           "elapsed_s": 21.7},
            expected={"width": 1920, "height": 1080, "duration": 5.0,
                      "require_audio": True})
        # static 4 + params 6 + render 3 + output 6 = 19
        assert report.summary["total"] == 19
        assert report.summary["passed"] == 19
        assert report.summary["failed"] == 0
        assert report.status == CheckStatus.PASS
