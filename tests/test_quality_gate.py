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
from conftest import standard_params, make_personal_zip


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

    def test_personal_schema_unreadable_warns(self, personal_tpl_v2):
        """契约不可读时不臆断必填项，只提示（旧实现硬编码 timeline 属误判）。"""
        results = QualityGate().check_params(str(personal_tpl_v2), {"x": 1})
        assert not any(c.name == "required_timeline" and c.status == CheckStatus.FAIL
                       for c in results)
        assert any(c.name == "schema_unreadable" and c.status == CheckStatus.WARN
                   for c in results)

    def test_personal_timeline_required_by_schema(self, tmp_path):
        """包内 schema 真的声明了 timeline 时才判缺失。"""
        tpl = make_personal_zip(tmp_path / "test_personal_tl.motion",
                                schema_required=["timeline"])
        results = QualityGate().check_params(str(tpl), {"x": 1})
        assert any(c.name == "required_timeline" and c.status == CheckStatus.FAIL
                   for c in results)

    def test_personal_valid_timeline(self, personal_tpl_v2):
        results = QualityGate().check_params(
            str(personal_tpl_v2), {"timeline": {"segments": [{"id": "s1"}]}})
        assert all(c.status != CheckStatus.FAIL for c in results)


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

    @staticmethod
    def _fragment_like_frames(width=320, height=180, n=12, backdrop=128,
                              patch=40, step=24):
        """片段类模板仿真：大面积纯色底 + 小面积主体在横向移动。

        全局亮度 Δmean 几乎不变（白块总数恒定），逐像素平均差也被大背景稀释，
        只有"最活跃块"的分块差能反映主体在动 —— 这是 2026-11-21 用真实
        水墨片段成片（Δmean=0.10 / Δpx=0.26 / Δblk=7.55）标定的场景。
        """
        fb = width * height * 3

        def frame(offset):
            buf = bytearray([backdrop]) * fb
            for y in range(40, 40 + patch // 2):
                for x in range(offset, min(width, offset + patch)):
                    for c in range(3):
                        buf[(y * width + x) * 3 + c] = 255
            return bytes(buf)

        # 白块沿横向移动；每块 20px 高，保证落在分块网格内
        frames = [frame(20 + i * step) for i in range(n)]
        return frames, {"width": width, "height": height, "duration": 10.0}

    def test_fragment_like_moving_passes(self, tmp_path, monkeypatch):
        """片段类：局部主体移动、全局亮度不变 → 不得判"整片静止"。"""
        monkeypatch.setattr(
            "core.quality_gate.gate.QualityGate._ffmpeg_extract_frames",
            lambda self, p, n=12: TestOutputFrameChecks._fragment_like_frames())
        f = tmp_path / "frag.mp4"
        f.write_bytes(b"x")
        results = QualityGate().check_output_frames(str(f))
        assert any(c.name == "frame_static" and c.status == CheckStatus.PASS
                   for c in results)

    def test_block_diff_ignores_static_backdrop(self):
        """分块差分：整幅静止 → 0 附近；小块移动 → 明显大于阈值。"""
        frames, meta = self._fragment_like_frames(n=2, step=60)
        a, b = frames
        diff = QualityGate._frame_block_diff(a, b, meta["width"], meta["height"])
        assert diff > QualityGate.STATIC_FRAME_BLOCK_DIFF
        assert QualityGate._frame_block_diff(a, a, meta["width"], meta["height"]) == 0.0

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
        """Core regression: standard template full gate == 28/28 PASS
        (static 4 + params 6 + determinism 2 + render 3 + output 6
         + stream 3 + frames 2 + audio 2)."""
        def fake_probe(p):
            return {
                "streams": [{"codec_type": "video", "codec_name": "h264",
                             "width": 1920, "height": 1080},
                            {"codec_type": "audio", "codec_name": "aac"}],
                "format": {"duration": "5.0"},
            }
        monkeypatch.setattr("core.quality_gate.gate._ffprobe", fake_probe)
        # B-01 三项在 TestOutputStreamHardVerify 单验；此处只验流水线挂载
        monkeypatch.setattr(
            "core.quality_gate.gate.QualityGate.check_output_stream",
            lambda self, p, e=None: [
                CheckResult("output", "container_header", CheckStatus.PASS, "box ok"),
                CheckResult("output", "full_decode", CheckStatus.PASS, "decode ok"),
                CheckResult("output", "seek_frames", CheckStatus.PASS, "seek ok"),
            ])
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
        #   + stream 3 + frames 2 + audio 2 = 28
        assert report.summary["total"] == 28
        assert report.summary["passed"] == 28
        assert report.summary["failed"] == 0
        assert report.status == CheckStatus.PASS


# ── B-01 导出硬验证（容器头 / 全解码 / 代表帧 seek）──────────────

def _box(btype: bytes, payload: bytes = b"") -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + btype + payload


def _fake_mp4(moov_first: bool = True, truncate: bool = False) -> bytes:
    """构造最小 ISO-BMFF 盒链：ftyp + moov + mdat（可调顺序/可截断）。"""
    ftyp = _box(b"ftyp", b"isom" + b"\x00\x00\x02\x00" + b"isomiso2avc1mp41")
    moov = _box(b"moov", b"\x00" * 40)
    mdat = _box(b"mdat", b"\xff" * 200)
    data = b"".join([ftyp, moov, mdat] if moov_first else [ftyp, mdat, moov])
    return data[:-60] if truncate else data


class FakeStreamProc:
    """subprocess.run 替身：按是否带 -ss 区分"取帧"与"全解码"。"""

    def __init__(self, decode_rc=0, decode_err=b"", frame_bytes=b"x" * 1000):
        self.returncode = decode_rc
        self.stdout = b""
        self.stderr = decode_err
        self._frame = frame_bytes

    def __call__(self, cmd, **kwargs):
        proc = FakeStreamProc.__new__(FakeStreamProc)
        proc.returncode = 0
        proc.stderr = b""
        proc.stdout = self._frame if "-ss" in cmd else b""
        if "-ss" not in cmd:
            proc.returncode = self.returncode
            proc.stderr = self.stderr
        return proc


@pytest.fixture
def mp4_for_gate():
    def _make(tmp_path, **kwargs):
        f = tmp_path / "out.mp4"
        f.write_bytes(_fake_mp4(**kwargs))
        return f
    return _make


class TestOutputStreamHardVerify:
    """B-01：导出硬验证三项——不看规格，只看"流能不能真解"。"""

    def _patch_probe(self, monkeypatch, duration="5.0"):
        monkeypatch.setattr(
            "core.quality_gate.gate._ffprobe",
            lambda p: {"streams": [{"codec_type": "video", "codec_name": "h264",
                                    "width": 1920, "height": 1080}],
                       "format": {"duration": duration}})

    def test_container_faststart_pass(self, tmp_path, mp4_for_gate, monkeypatch):
        self._patch_probe(monkeypatch)
        monkeypatch.setattr("core.quality_gate.gate.subprocess.run", FakeStreamProc())
        f = mp4_for_gate(tmp_path)
        results = QualityGate().check_output_stream(str(f))
        names = {c.name: c for c in results}
        assert set(names) == {"container_header", "full_decode", "seek_frames"}
        assert names["container_header"].status == CheckStatus.PASS
        assert "faststart=是" in names["container_header"].message
        assert names["full_decode"].status == CheckStatus.PASS
        assert names["seek_frames"].status == CheckStatus.PASS
        assert "3/3" in names["seek_frames"].message

    def test_slow_start_fails_only_when_required(self, tmp_path, mp4_for_gate,
                                                 monkeypatch):
        self._patch_probe(monkeypatch)
        monkeypatch.setattr("core.quality_gate.gate.subprocess.run", FakeStreamProc())
        f = mp4_for_gate(tmp_path, moov_first=False)
        ok = QualityGate().check_output_stream(str(f))
        assert any(c.name == "container_header" and c.status == CheckStatus.PASS
                   for c in ok)
        bad = QualityGate().check_output_stream(str(f), {"require_faststart": True})
        assert any(c.name == "container_header" and c.status == CheckStatus.FAIL
                   for c in bad)

    def test_truncated_file_detected(self, tmp_path, mp4_for_gate, monkeypatch):
        self._patch_probe(monkeypatch)
        f = mp4_for_gate(tmp_path, truncate=True)
        results = QualityGate().check_output_stream(str(f))
        assert len(results) == 1                      # 结构不可信 → 不再解码
        assert results[0].name == "container_header"
        assert results[0].status == CheckStatus.FAIL
        assert "截断" in results[0].message

    def test_missing_moov_detected(self, tmp_path, monkeypatch):
        f = tmp_path / "nomoov.mp4"
        f.write_bytes(_box(b"ftyp", b"isom") + _box(b"mdat", b"\xff" * 100))
        results = QualityGate().check_output_stream(str(f))
        assert results[0].status == CheckStatus.FAIL
        assert "moov" in results[0].message

    def test_non_mp4_skips_with_warn(self, tmp_path):
        f = tmp_path / "pic.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        results = QualityGate().check_output_stream(str(f))
        assert len(results) == 1
        assert results[0].status == CheckStatus.WARN
        assert "跳过容器头解析" in results[0].message

    def test_full_decode_reports_ffmpeg_error(self, tmp_path, mp4_for_gate,
                                              monkeypatch):
        self._patch_probe(monkeypatch)
        monkeypatch.setattr("core.quality_gate.gate.subprocess.run",
                            FakeStreamProc(decode_rc=1,
                                           decode_err=b"Invalid data found\n"))
        f = mp4_for_gate(tmp_path)
        results = QualityGate().check_output_stream(str(f))
        named = next(c for c in results if c.name == "full_decode")
        assert named.status == CheckStatus.FAIL
        assert "Invalid data found" in named.message

    def test_seek_frames_fail_when_frame_empty(self, tmp_path, mp4_for_gate,
                                               monkeypatch):
        self._patch_probe(monkeypatch)
        monkeypatch.setattr("core.quality_gate.gate.subprocess.run",
                            FakeStreamProc(frame_bytes=b""))
        f = mp4_for_gate(tmp_path)
        results = QualityGate().check_output_stream(str(f))
        named = next(c for c in results if c.name == "seek_frames")
        assert named.status == CheckStatus.FAIL
        assert "代表帧取不到" in named.message

    def test_seek_skipped_without_duration(self, tmp_path, mp4_for_gate, monkeypatch):
        self._patch_probe(monkeypatch, duration="0")
        monkeypatch.setattr("core.quality_gate.gate.subprocess.run", FakeStreamProc())
        f = mp4_for_gate(tmp_path)
        results = QualityGate().check_output_stream(str(f))
        named = next(c for c in results if c.name == "seek_frames")
        assert named.status == CheckStatus.WARN
