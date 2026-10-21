"""Quality gate implementation with four inspection layers.

Layers:
  1. static  - template file integrity & structure
  2. params  - parameter validation
  3. render  - render process monitoring
  4. output  - output file quality verification
"""

import json
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from ..template_parser import detect_format, parse_template


class CheckStatus(Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class CheckResult:
    """Result of a single quality check."""
    stage: str  # static, params, render, output
    name: str
    status: CheckStatus
    message: str = ""
    duration_ms: int = 0


@dataclass
class QualityReport:
    """Complete quality inspection report."""
    task_id: str = ""
    template: str = ""
    status: CheckStatus = CheckStatus.PASS
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def summary(self) -> dict:
        return {
            "total": len(self.checks),
            "passed": sum(1 for c in self.checks if c.status == CheckStatus.PASS),
            "warnings": sum(1 for c in self.checks if c.status == CheckStatus.WARN),
            "failed": sum(1 for c in self.checks if c.status == CheckStatus.FAIL),
        }

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "template": self.template,
            "status": self.status.value,
            "summary": self.summary,
            "checks": [
                {
                    "stage": c.stage,
                    "name": c.name,
                    "status": c.status.value,
                    "message": c.message,
                    "duration_ms": c.duration_ms,
                }
                for c in self.checks
            ],
        }


def _ffprobe(path: str) -> Optional[dict]:
    """Run ffprobe on a media file and return parsed JSON, or None on failure."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,codec_name,width,height,r_frame_rate,duration",
             "-show_entries", "format=duration,size",
             "-of", "json", path],
            capture_output=True, timeout=30
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout.decode("utf-8", errors="replace"))
    except Exception:
        return None


class QualityGate:
    """Four-layer quality gate: static -> params -> render -> output."""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.ffprobe_path = self.config.get("ffprobe_path", "ffprobe")

    # ── Layer 1: Static ──────────────────────────────────────────

    def check_static(self, template_path: str) -> list[CheckResult]:
        """Layer 1: Template file integrity & structure checks."""
        results = []
        t0 = time.time()
        path = Path(template_path)

        # 1.1 File exists
        if not path.exists():
            results.append(CheckResult("static", "file_exists", CheckStatus.FAIL,
                                       f"模板文件不存在: {template_path}"))
            return results
        results.append(CheckResult("static", "file_exists", CheckStatus.PASS,
                                   f"文件大小: {path.stat().st_size} bytes"))

        # 1.2 File is a valid ZIP (.motion is ZIP-based)
        import zipfile
        if not zipfile.is_zipfile(path):
            results.append(CheckResult("static", "zip_valid", CheckStatus.FAIL,
                                       "不是有效的 ZIP/.motion 文件"))
            return results
        results.append(CheckResult("static", "zip_valid", CheckStatus.PASS))

        # 1.3 Format detection
        try:
            fmt = detect_format(template_path)
            results.append(CheckResult("static", "format_detect", CheckStatus.PASS,
                                       f"格式: {fmt}"))
        except Exception as e:
            results.append(CheckResult("static", "format_detect", CheckStatus.FAIL,
                                       f"格式检测失败: {e}"))
            return results

        # 1.4 Template parse
        try:
            info = parse_template(template_path)
            results.append(CheckResult("static", "template_parse", CheckStatus.PASS,
                                       f"模板: {info.name}, 运行时: {info.runtime_profile}"))
        except Exception as e:
            results.append(CheckResult("static", "template_parse", CheckStatus.FAIL,
                                       f"模板解析失败: {e}"))

        for r in results:
            r.duration_ms = int((time.time() - t0) * 1000)
        return results

    # ── Layer 2: Params ──────────────────────────────────────────

    def check_params(self, template_path: str, params: dict) -> list[CheckResult]:
        """Layer 2: Parameter validation (basic checks; full schema in B4)."""
        results = []
        t0 = time.time()

        # 2.1 Params is a non-empty dict
        if not isinstance(params, dict) or not params:
            results.append(CheckResult("params", "params_type", CheckStatus.FAIL,
                                       "参数必须是非空字典"))
            return results
        results.append(CheckResult("params", "params_type", CheckStatus.PASS,
                                   f"{len(params)} 个字段"))

        # 2.2 No BOM in JSON if params came from file (checked at load time)
        # Here we check for common required fields based on format
        try:
            fmt = detect_format(template_path)
        except Exception:
            fmt = "unknown"

        if fmt == "standard":
            # standard format requires mode, language, layout, scenes
            required = ["mode", "language", "layout", "scenes"]
            for field_name in required:
                if field_name not in params:
                    results.append(CheckResult("params", f"required_{field_name}",
                                               CheckStatus.FAIL,
                                               f"standard 格式缺少必填字段: {field_name}"))
                else:
                    results.append(CheckResult("params", f"required_{field_name}",
                                               CheckStatus.PASS))
            # scenes must be a non-empty list
            scenes = params.get("scenes", [])
            if not isinstance(scenes, list) or len(scenes) == 0:
                results.append(CheckResult("params", "scenes_nonempty", CheckStatus.FAIL,
                                           "scenes 必须是非空列表"))
            elif len(scenes) > 30:
                results.append(CheckResult("params", "scenes_limit", CheckStatus.WARN,
                                           f"scenes 数量 {len(scenes)} 超过建议上限 30"))
            else:
                results.append(CheckResult("params", "scenes_nonempty", CheckStatus.PASS,
                                           f"{len(scenes)} 个场景"))

        elif fmt == "personal-download-v1":
            # personal format requires timeline
            if "timeline" not in params:
                results.append(CheckResult("params", "required_timeline", CheckStatus.FAIL,
                                           "personal 格式缺少必填字段: timeline"))
            else:
                results.append(CheckResult("params", "required_timeline", CheckStatus.PASS))
                tl = params["timeline"]
                if "segments" not in tl or not isinstance(tl.get("segments"), list):
                    results.append(CheckResult("params", "timeline_segments", CheckStatus.FAIL,
                                               "timeline.segments 必须是列表"))
                else:
                    results.append(CheckResult("params", "timeline_segments", CheckStatus.PASS,
                                               f"{len(tl['segments'])} 个片段"))

        for r in results:
            r.duration_ms = int((time.time() - t0) * 1000)
        return results

    # ── Layer 3: Render ──────────────────────────────────────────

    def check_render(self, render_result: dict) -> list[CheckResult]:
        """Layer 3: Render process monitoring & result checks."""
        results = []
        t0 = time.time()

        # 3.1 Render success
        if not render_result.get("success", False):
            results.append(CheckResult("render", "render_success", CheckStatus.FAIL,
                                       render_result.get("error", "渲染失败")))
            return results
        results.append(CheckResult("render", "render_success", CheckStatus.PASS))

        # 3.2 Output file exists
        out_path = render_result.get("output_path", "")
        if not out_path or not Path(out_path).exists():
            results.append(CheckResult("render", "output_exists", CheckStatus.FAIL,
                                       f"输出文件不存在: {out_path}"))
        else:
            size = Path(out_path).stat().st_size
            results.append(CheckResult("render", "output_exists", CheckStatus.PASS,
                                       f"文件大小: {size} bytes"))

        # 3.3 Render duration sanity
        elapsed = render_result.get("elapsed_s", 0)
        if elapsed <= 0:
            results.append(CheckResult("render", "duration_sanity", CheckStatus.WARN,
                                       "渲染时长为 0，可能未实际渲染"))
        elif elapsed > 600:
            results.append(CheckResult("render", "duration_sanity", CheckStatus.WARN,
                                       f"渲染耗时 {elapsed:.1f}s，超过 10 分钟"))
        else:
            results.append(CheckResult("render", "duration_sanity", CheckStatus.PASS,
                                       f"{elapsed:.1f}s"))

        for r in results:
            r.duration_ms = int((time.time() - t0) * 1000)
        return results

    # ── Layer 4: Output ──────────────────────────────────────────

    def check_output(self, output_path: str, expected: Optional[dict] = None) -> list[CheckResult]:
        """Layer 4: Output file quality verification via ffprobe."""
        results = []
        t0 = time.time()
        expected = expected or {}

        # 4.1 File exists & non-empty
        path = Path(output_path)
        if not path.exists():
            results.append(CheckResult("output", "file_exists", CheckStatus.FAIL,
                                       f"输出文件不存在: {output_path}"))
            return results
        if path.stat().st_size == 0:
            results.append(CheckResult("output", "file_nonempty", CheckStatus.FAIL,
                                       "输出文件为空"))
            return results
        results.append(CheckResult("output", "file_exists", CheckStatus.PASS,
                                   f"{path.stat().st_size} bytes"))

        # 4.2 ffprobe decodable
        probe = _ffprobe(output_path)
        if probe is None:
            results.append(CheckResult("output", "decodable", CheckStatus.FAIL,
                                       "ffprobe 无法解码，文件可能损坏"))
            return results
        results.append(CheckResult("output", "decodable", CheckStatus.PASS))

        # 4.3 Video stream exists
        streams = probe.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        if not video:
            results.append(CheckResult("output", "video_stream", CheckStatus.FAIL,
                                       "没有视频流"))
        else:
            results.append(CheckResult("output", "video_stream", CheckStatus.PASS,
                                       f"编码: {video.get('codec_name')}"))

        # 4.4 Resolution check
        if video:
            w, h = video.get("width", 0), video.get("height", 0)
            exp_w = expected.get("width")
            exp_h = expected.get("height")
            if exp_w and w != exp_w:
                results.append(CheckResult("output", "resolution", CheckStatus.FAIL,
                                           f"宽度 {w} != 期望 {exp_w}"))
            elif exp_h and h != exp_h:
                results.append(CheckResult("output", "resolution", CheckStatus.FAIL,
                                           f"高度 {h} != 期望 {exp_h}"))
            else:
                results.append(CheckResult("output", "resolution", CheckStatus.PASS,
                                           f"{w}x{h}"))

        # 4.5 Duration check
        fmt_duration = float(probe.get("format", {}).get("duration", 0))
        exp_dur = expected.get("duration")
        if exp_dur and abs(fmt_duration - exp_dur) > 0.5:
            results.append(CheckResult("output", "duration", CheckStatus.WARN,
                                       f"时长 {fmt_duration:.1f}s 与期望 {exp_dur}s 偏差 >0.5s"))
        elif fmt_duration <= 0:
            results.append(CheckResult("output", "duration", CheckStatus.FAIL,
                                       "时长为 0"))
        else:
            results.append(CheckResult("output", "duration", CheckStatus.PASS,
                                       f"{fmt_duration:.1f}s"))

        # 4.6 Audio stream check (warn if missing for personal format)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        if expected.get("require_audio") and not audio:
            results.append(CheckResult("output", "audio_stream", CheckStatus.WARN,
                                       "期望有音频流但未找到"))
        elif audio:
            results.append(CheckResult("output", "audio_stream", CheckStatus.PASS,
                                       f"编码: {audio.get('codec_name')}"))

        for r in results:
            r.duration_ms = int((time.time() - t0) * 1000)
        return results

    # ── Combined ─────────────────────────────────────────────────

    def check(self, template_path: str, params: dict,
              output_path: Optional[str] = None,
              render_result: Optional[dict] = None,
              expected: Optional[dict] = None,
              task_id: str = "") -> QualityReport:
        """Run all applicable checks and return QualityReport.

        Args:
            template_path: Path to .motion template
            params: Render parameters
            output_path: Path to rendered output (if available)
            render_result: Result dict from renderer (if available)
            expected: Expected output properties {width, height, duration, require_audio}
            task_id: Optional task identifier
        """
        report = QualityReport(task_id=task_id, template=template_path)
        all_checks = []

        # Layer 1 & 2 always run
        all_checks.extend(self.check_static(template_path))
        all_checks.extend(self.check_params(template_path, params))

        # Layer 3: only if render_result provided
        if render_result is not None:
            all_checks.extend(self.check_render(render_result))

        # Layer 4: only if output_path provided
        if output_path is not None:
            all_checks.extend(self.check_output(output_path, expected))

        report.checks = all_checks

        # Determine overall status
        if any(c.status == CheckStatus.FAIL for c in all_checks):
            report.status = CheckStatus.FAIL
        elif any(c.status == CheckStatus.WARN for c in all_checks):
            report.status = CheckStatus.WARN
        else:
            report.status = CheckStatus.PASS

        return report
