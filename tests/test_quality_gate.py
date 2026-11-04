# -*- coding: utf-8 -*-
"""Tests for core.quality_gate (four layers + determinism + 25/25 equivalent report)."""
import io
import json
import sys
import zipfile
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


class TestDeterminismLayer:
    """P1: 渲染前静态确定性检查（源码时钟/随机风险 + 同轨重叠）。"""

    def test_standard_clean_source(self, standard_tpl):
        results = QualityGate().check_determinism_static(
            str(standard_tpl), standard_params())
        assert any(c.name == "clock_risk" and c.status == CheckStatus.PASS
                   for c in results)
        # standard 无 timeline.segments -> 跳过并 PASS
        assert any(c.name == "clip_overlap" and c.status == CheckStatus.PASS
                   for c in results)

    def test_clock_risk_detected(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("motion.json", "{}")
            z.writestr("adapter.py",
                       "def plan():\n    t = Date.now()\n    r = Math.random()\n")
            z.writestr("render.mjs", "export default {}\n")
        f = tmp_path / "risky.motion"
        f.write_bytes(buf.getvalue())
        results = QualityGate().check_determinism_static(str(f), {"mode": "standard"})
        hits = [c for c in results if c.name == "clock_risk"]
        assert hits and all(c.status == CheckStatus.WARN for c in hits)
        assert "P0-b" in hits[0].message

    def test_clock_risk_detected_dir(self, tmp_path):
        """目录型模板（本地模板形态）同样可扫描。"""
        d = tmp_path / "tpl_dir"
        d.mkdir()
        (d / "native").mkdir()
        (d / "native" / "film.js").write_text(
            "const t = performance.now();\n", encoding="utf-8")
        (d / "motion.json").write_text("{}", encoding="utf-8")
        results = QualityGate().check_determinism_static(str(d), {"mode": "standard"})
        hits = [c for c in results if c.name == "clock_risk"]
        assert hits and all(c.status == CheckStatus.WARN for c in hits)

    def test_clip_overlap_detected(self, personal_tpl_v2):
        params = {"timeline": {"segments": [
            {"id": "s1", "start": 0, "end": 5},
            {"id": "s2", "start": 4, "end": 9},
        ]}}
        results = QualityGate().check_determinism_static(str(personal_tpl_v2), params)
        assert any(c.name == "clip_overlap" and c.status == CheckStatus.FAIL
                   for c in results)

    def test_clip_no_overlap(self, personal_tpl_v2):
        params = {"timeline": {"segments": [
            {"id": "s1", "start": 0, "end": 5},
            {"id": "s2", "start": 5, "end": 9},
        ]}}
        results = QualityGate().check_determinism_static(str(personal_tpl_v2), params)
        assert any(c.name == "clip_overlap" and c.status == CheckStatus.PASS
                   for c in results)

    def test_check_clip_overlap_unit(self):
        g = QualityGate()
        assert g._check_clip_overlap([]) == ""
        assert g._check_clip_overlap([
            {"id": "a", "start": 0, "end": 3},
            {"id": "b", "start": 2.5, "end": 4},
        ]) != ""
        assert g._check_clip_overlap([
            {"id": "a", "start": 0, "end": 3},
            {"id": "b", "start": 3, "end": 4},
        ]) == ""


class TestOutputFrameChecks:
    """P1: 输出级画面确定性（flat_frame 纯色 + frame_static 静止）。"""

    @staticmethod
    def _vary_frames(width=320, height=180, n=12):
        fb = width * height * 3
        frames = [bytes((j * 3 + i * 7) % 256 for j in range(fb))
                  for i in range(n)]
        return frames, {"width": width, "height": height, "duration": 10.0}

    @staticmethod
    def _flat_frames(width=320, height=180, n=12):
        fb = width * height * 3
        return [bytes([0]) * fb] * n, {"width": width, "height": height,
                                       "duration": 10.0}

    def test_flat_frame_all_pure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "core.quality_gate.gate.QualityGate._ffmpeg_extract_frames",
            lambda self, p, n=12: TestOutputFrameChecks._flat_frames())
        f = tmp_path / "flat.mp4"
        f.write_bytes(b"x")
        results = QualityGate().check_output_frames(str(f))
        assert any(c.name == "flat_frame" and c.status == CheckStatus.FAIL
                   for c in results)
        assert any(c.name == "frame_static" and c.status == CheckStatus.WARN
                   for c in results)

    def test_frames_vary_ok(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "core.quality_gate.gate.QualityGate._ffmpeg_extract_frames",
            lambda self, p, n=12: TestOutputFrameChecks._vary_frames())
        f = tmp_path / "vary.mp4"
        f.write_bytes(b"x")
        results = QualityGate().check_output_frames(str(f))
        assert all(c.status == CheckStatus.PASS for c in results)

    def test_extract_failure_warns(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "core.quality_gate.gate.QualityGate._ffmpeg_extract_frames",
            lambda self, p, n=12: ([], None))
        f = tmp_path / "noextract.mp4"
        f.write_bytes(b"x")
        results = QualityGate().check_output_frames(str(f))
        assert any(c.name == "frame_extract" and c.status == CheckStatus.WARN
                   for c in results)


class TestFrameExtractionCommand:
    """回归（2026-11-05）：ffmpeg 抽帧命令须适配新版 ffmpeg，且限制输出帧数。

    ffmpeg 7.x 移除了 -vsync 选项；旧命令会让抽帧永远失败，
    导致画面确定性检查静默降级成 WARN。此处直接断言命令行契约。
    """

    @staticmethod
    def _probe_stub(width=320, height=180, duration=10.0, fps="30/1"):
        return {"streams": [{"codec_type": "video", "codec_name": "h264",
                             "width": width, "height": height,
                             "r_frame_rate": fps}],
                "format": {"duration": str(duration)}}

    def test_command_has_no_deprecated_vsync(self, monkeypatch):
        captured = {}

        class FakeProc:
            returncode = 0
            stdout = b"\x00" * (320 * 180 * 3) * 3
            stderr = b""

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return FakeProc()

        monkeypatch.setattr("core.quality_gate.gate._ffprobe",
                            lambda p: TestFrameExtractionCommand._probe_stub())
        monkeypatch.setattr("core.quality_gate.gate.subprocess.run", fake_run)
        frames, meta = QualityGate()._ffmpeg_extract_frames("x.mp4", n=2)
        cmd = captured["cmd"]
        assert "-vsync" not in cmd, "旧版 -vsync 在新 ffmpeg 上已移除，会让抽帧永久失败"
        assert "-frames:v" in cmd, "必须限制输出帧数，否则长视频会把全帧 raw 灌进内存"
        vf = cmd[cmd.index("-vf") + 1]
        assert "select=" in vf
        assert "-an" in cmd
        assert len(frames) == 2
        assert meta["width"] == 320


class TestOutputAudioChecks:
    """P1: 输出级音频（峰值太小声 + LUFS 可选）。"""

    @staticmethod
    def _fake_run(text):
        class FakeProc:
            returncode = 0
            stderr = text.encode("utf-8")
            stdout = b""
        return FakeProc

    def test_peak_ok(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.quality_gate.gate.subprocess.run",
                            lambda *a, **k: self._fake_run(
                                "mean_volume: -19.3 dB\nmax_volume: -3.5 dB\n"))
        f = tmp_path / "a.mp4"
        f.write_bytes(b"x")
        results = QualityGate().check_output_audio(str(f))
        assert any(c.name == "audio_peak" and c.status == CheckStatus.PASS
                   for c in results)

    def test_peak_quiet_warns(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.quality_gate.gate.subprocess.run",
                            lambda *a, **k: self._fake_run(
                                "mean_volume: -43.0 dB\nmax_volume: -40.0 dB\n"))
        f = tmp_path / "q.mp4"
        f.write_bytes(b"x")
        results = QualityGate().check_output_audio(str(f))
        assert any(c.name == "audio_peak" and c.status == CheckStatus.WARN
                   for c in results)
        assert any("-30" in c.message for c in results
                   if c.name == "audio_peak")

    def test_lufs_missing_lib_warns(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.quality_gate.gate.subprocess.run",
                            lambda *a, **k: self._fake_run(
                                "mean_volume: -19.3 dB\nmax_volume: -3.5 dB\n"))
        monkeypatch.setitem(sys.modules, "soundfile", None)
        monkeypatch.setitem(sys.modules, "pyloudnorm", None)
        f = tmp_path / "m.mp4"
        f.write_bytes(b"x")
        results = QualityGate().check_output_audio(str(f))
        assert any(c.name == "loudness_lufs" and c.status == CheckStatus.WARN
                   for c in results)

    def test_lufs_ok_with_libs(self, tmp_path, monkeypatch):
        class FakeMeter:
            def __init__(self, rate):
                pass
            def integrated_loudness(self, data):
                return -16.0
        class FakePyln:
            Meter = FakeMeter
        class FakeSf:
            @staticmethod
            def read(path, dtype="float32"):
                return __import__("numpy").zeros((44100, 2), dtype="float32"), 44100
        monkeypatch.setattr("core.quality_gate.gate.subprocess.run",
                            lambda *a, **k: self._fake_run(
                                "mean_volume: -19.3 dB\nmax_volume: -3.5 dB\n"))
        monkeypatch.setitem(sys.modules, "soundfile", FakeSf())
        monkeypatch.setitem(sys.modules, "pyloudnorm", FakePyln())
        f = tmp_path / "l.mp4"
        f.write_bytes(b"x")
        results = QualityGate().check_output_audio(str(f))
        assert any(c.name == "loudness_lufs" and c.status == CheckStatus.PASS
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
        # static(4) + params(6) + determinism(2) = 12, all pass
        assert report.status == CheckStatus.PASS
        assert report.summary["passed"] == 12
        assert report.summary["failed"] == 0

    def test_full_25_25_equivalent(self, standard_tpl, tmp_path, monkeypatch):
        """Core regression: standard template full gate == 25/25 PASS
        (static 4 + params 6 + determinism 2 + render 3 + output 6
         + frames 2 + audio 2)."""
        def fake_probe(p):
            return {
                "streams": [{"codec_type": "video", "codec_name": "h264",
                             "width": 1920, "height": 1080},
                            {"codec_type": "audio", "codec_name": "aac"}],
                "format": {"duration": "5.0"},
            }
        monkeypatch.setattr("core.quality_gate.gate._ffprobe", fake_probe)
        # 确定性画面：有内容、随时间变化
        monkeypatch.setattr(
            "core.quality_gate.gate.QualityGate._ffmpeg_extract_frames",
            lambda self, p, n=12: TestOutputFrameChecks._vary_frames())
        # 音频峰值正常
        class FakeProc:
            returncode = 0
            stderr = b"mean_volume: -19.3 dB\nmax_volume: -3.5 dB\n"
            stdout = b""
        monkeypatch.setattr("core.quality_gate.gate.subprocess.run",
                            lambda *a, **k: FakeProc())
        # LUFS 可用
        class FakeMeter:
            def __init__(self, rate):
                pass
            def integrated_loudness(self, data):
                return -16.0
        class FakePyln:
            Meter = FakeMeter
        class FakeSf:
            @staticmethod
            def read(path, dtype="float32"):
                return __import__("numpy").zeros((44100, 2), dtype="float32"), 44100
        monkeypatch.setitem(sys.modules, "soundfile", FakeSf())
        monkeypatch.setitem(sys.modules, "pyloudnorm", FakePyln())

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
        # static 4 + params 6 + determinism 2 + render 3 + output 6
        #   + frames 2 + audio 2 = 25
        assert report.summary["total"] == 25
        assert report.summary["passed"] == 25
        assert report.summary["failed"] == 0
        assert report.status == CheckStatus.PASS
