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


def _read_params_schema(template_path: str) -> Optional[dict]:
    """Read a package's declared params.schema.json (outer or nested content).

    Returns None when the contract cannot be read; callers must not invent a
    requirement in that case.
    """
    path = Path(template_path)
    try:
        if path.is_dir():
            for cand in (path / "params.schema.json",):
                if cand.is_file():
                    return json.loads(cand.read_text(encoding="utf-8"))
            return None
        import zipfile
        with zipfile.ZipFile(path) as outer:
            if "params.schema.json" in outer.namelist():
                return json.loads(outer.read("params.schema.json").decode("utf-8"))
            rel = next((n for n in outer.namelist() if n.endswith("release.bin")), None)
            if rel is None:
                return None
            payload = outer.read(rel)
            if payload[:4] != b"PK\x03\x04":
                return None
            import io
            with zipfile.ZipFile(io.BytesIO(payload)) as inner:
                if "params.schema.json" not in inner.namelist():
                    return None
                return json.loads(inner.read("params.schema.json").decode("utf-8"))
    except Exception:
        return None



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
            # personal 包的参数契约以包内 params.schema.json 为准：
            # 只有声明了 timeline 的模板才要求 timeline（默认 html-canvas 档为
            # 片段类参数契约，不含 timeline，不应被判缺失）。
            schema = _read_params_schema(template_path)
            required = schema.get("required", []) if isinstance(schema, dict) else []
            for field_name in required:
                if field_name in params:
                    results.append(CheckResult("params", f"required_{field_name}",
                                               CheckStatus.PASS))
                else:
                    results.append(CheckResult("params", f"required_{field_name}",
                                               CheckStatus.FAIL,
                                               f"schema 必填字段缺失: {field_name}"))
            if "timeline" in params:
                results.append(CheckResult("params", "required_timeline", CheckStatus.PASS))
                tl = params["timeline"]
                if "segments" not in tl or not isinstance(tl.get("segments"), list):
                    results.append(CheckResult("params", "timeline_segments", CheckStatus.FAIL,
                                               "timeline.segments 必须是列表"))
                else:
                    results.append(CheckResult("params", "timeline_segments", CheckStatus.PASS,
                                               f"{len(tl['segments'])} 个片段"))
            elif schema is None:
                # 读不到契约时不做臆断，只记录一条提示
                results.append(CheckResult("params", "schema_unreadable", CheckStatus.WARN,
                                           "包内 params.schema.json 不可读，按参数原样放行"))

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

    # ── 导出硬验证（B-01）─────────────────────────────────────────
    # 现有 4.x 检查只看"规格对不对"（ffprobe 元数据），不看"流能不能真解"。
    # 这三项补上硬证据：容器头结构完整、全流可解码、代表帧可 seek 取出。

    # 认为"可当 MP4/ISO-BMFF 处理"的首个 box 类型
    MP4_HEAD_BOXES = {b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide"}
    SEEK_POINTS = (0.1, 0.5, 0.9)   # 代表帧采样位置（按总时长比例）
    SEEK_MIN_FRAME_BYTES = 512      # 一帧 PNG 小于此值视为没取到

    @staticmethod
    def _parse_mp4_boxes(path: str):
        """解析顶层 box 链，返回 (boxes, error)。

        boxes: [(type, offset, size)]；error 非空表示结构不可信（越界/非法）。
        """
        try:
            total = Path(path).stat().st_size
            boxes = []
            with open(path, "rb") as fh:
                offset = 0
                while offset + 8 <= total:
                    fh.seek(offset)
                    header = fh.read(8)
                    if len(header) < 8:
                        return boxes, f"盒头读取不完整 @{offset}"
                    size = int.from_bytes(header[0:4], "big")
                    btype = header[4:8].decode("latin-1")
                    if not btype.strip():
                        return boxes, f"非法 box 类型 @{offset}"
                    if size == 1:                    # 64 位 largesize
                        large = fh.read(8)
                        if len(large) < 8:
                            return boxes, f"largesize 不完整 @{offset}"
                        size = int.from_bytes(large, "big")
                        if size < 16:
                            return boxes, f"largesize 非法 @{offset}: {size}"
                    elif size == 0:                  # 延伸到文件末尾
                        size = total - offset
                    elif size < 8:
                        return boxes, f"box 尺寸非法 @{offset}: {size}"
                    if offset + size > total:
                        return boxes, (f"box {btype!r} 越界：声明 {size} 字节，"
                                       f"文件只剩 {total - offset} 字节（疑似截断）")
                    boxes.append((btype, offset, size))
                    offset += size
            return boxes, None
        except Exception as exc:
            return [], f"读取失败: {exc}"

    def check_output_stream(self, output_path: str,
                            expected: Optional[dict] = None
                            ) -> list[CheckResult]:
        """B-01 导出硬验证：容器头 / 全解码 / 代表帧 seek 三项。"""
        results = []
        expected = expected or {}
        t0 = time.time()
        path = Path(output_path)
        if not path.exists() or path.stat().st_size == 0:
            return [CheckResult("output", "container_header", CheckStatus.FAIL,
                                "输出文件缺失或为空，无法验证流")]

        # ① 容器头结构：先看首盒类型决定这是不是 ISO-BMFF，再解析盒链
        try:
            with open(output_path, "rb") as fh:
                head = fh.read(8)
        except Exception as exc:
            return [CheckResult("output", "container_header", CheckStatus.FAIL,
                               f"读取失败: {exc}")]
        first_type = head[4:8] if len(head) >= 8 else b""
        boxes, err = self._parse_mp4_boxes(output_path)
        blocked = False
        if first_type not in self.MP4_HEAD_BOXES:
            blocked = True
            results.append(CheckResult("output", "container_header", CheckStatus.WARN,
                                       f"首盒 {first_type!r} 不是 ISO-BMFF，跳过容器头解析"))
        else:
            types = {b[0] for b in boxes}
            if err:
                blocked = True
                results.append(CheckResult("output", "container_header",
                                           CheckStatus.FAIL, err))
            elif "moov" not in types or "mdat" not in types:
                blocked = True
                missing = "moov" if "moov" not in types else "mdat"
                results.append(CheckResult("output", "container_header", CheckStatus.FAIL,
                                           f"缺少必需的 {missing} box（顶层 {sorted(types)}）"))
            else:
                moov_off = next(b[1] for b in boxes if b[0] == "moov")
                mdat_off = next(b[1] for b in boxes if b[0] == "mdat")
                faststart = moov_off < mdat_off
                msg = (f"box 链完整（{len(boxes)} 个，{path.stat().st_size} 字节）；"
                       f"moov@{moov_off}，faststart={'是' if faststart else '否'}")
                if expected.get("require_faststart") and not faststart:
                    results.append(CheckResult("output", "container_header",
                                               CheckStatus.FAIL,
                                               msg + "；要求 faststart 但 moov 在 mdat 之后"))
                else:
                    results.append(CheckResult("output", "container_header",
                                               CheckStatus.PASS, msg))

        # 容器结构不可信时不继续解码/取帧（避免在坏文件上白跑）
        if blocked:
            for r in results:
                r.duration_ms = int((time.time() - t0) * 1000)
            return results

        # ② 全解码：所有流解一遍，任何 error 级输出即判坏流
        try:
            proc = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", output_path,
                 "-map", "0", "-f", "null", "-"],
                capture_output=True, timeout=300)
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            if proc.returncode != 0 or stderr:
                detail = (stderr or f"退出码 {proc.returncode}").replace("\n", " ")
                results.append(CheckResult("output", "full_decode", CheckStatus.FAIL,
                                           f"解码报错：{detail[:300]}"))
            else:
                results.append(CheckResult("output", "full_decode", CheckStatus.PASS,
                                           "全流解码零错误"))
        except Exception as exc:
            results.append(CheckResult("output", "full_decode", CheckStatus.FAIL,
                                       f"解码执行失败: {exc}"))

        # ③ 代表帧 seek：按比例取点，各取一帧，取不到即判 FAIL
        probe = _ffprobe(output_path)
        duration = float((probe or {}).get("format", {}).get("duration", 0) or 0)
        if duration <= 0:
            results.append(CheckResult("output", "seek_frames", CheckStatus.WARN,
                                       "拿不到时长，跳过代表帧 seek"))
        else:
            ok, bad, detail = 0, [], []
            for frac in self.SEEK_POINTS:
                t = duration * frac
                try:
                    proc = subprocess.run(
                        ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}",
                         "-i", output_path, "-frames:v", "1",
                         "-f", "image2pipe", "-vcodec", "png", "-"],
                        capture_output=True, timeout=120)
                    got = len(proc.stdout)
                    if proc.returncode == 0 and got >= self.SEEK_MIN_FRAME_BYTES:
                        ok += 1
                        detail.append(f"{t:.2f}s:{got}B")
                    else:
                        bad.append(f"{t:.2f}s")
                except Exception:
                    bad.append(f"{t:.2f}s")
            if bad:
                results.append(CheckResult("output", "seek_frames", CheckStatus.FAIL,
                                           f"代表帧取不到：{', '.join(bad)}"
                                           f"（成功 {ok}/{len(self.SEEK_POINTS)}）"))
            else:
                results.append(CheckResult("output", "seek_frames", CheckStatus.PASS,
                                           f"{ok}/{len(self.SEEK_POINTS)} 点取帧成功："
                                           + " / ".join(detail)))

        for r in results:
            r.duration_ms = int((time.time() - t0) * 1000)
        return results

    # ── Determinism (P1, OpenFilm look 借鉴) ─────────────────────

    # 确定性风险源码模式（命中说明模板依赖真实时钟/未播种随机，
    # 需要 P0-b 时钟冻结或模板侧整改）
    CLOCK_RISK_PATTERNS = {
        "Date.now()": "Date.now()",
        "new Date(": "new Date(",
        "performance.now()": "performance.now()",
        "requestAnimationFrame": "requestAnimationFrame",
        "Math.random()": "Math.random()",
        "gsap.from(": "gsap.from(（from 型 tween 无显式起止，须改 fromTo）",
        ".from(": ".from(（from 型 tween 无显式起止，须改 fromTo）",
    }
    # OpenFilm SAME_LIMIT 对应容差（0-255 尺度，先按开源默认标定）
    FLAT_FRAME_VARIANCE = 6.0
    STATIC_FRAME_MAD = 2.0      # 帧间平均绝对差 < 2.0 视为"整片静止"
    STATIC_FRAME_PIXEL_DIFF = 1.5  # 逐像素平均差（同位置取样）< 1.5 视为"无空间变化"
    STATIC_FRAME_BLOCK_DIFF = 4.0  # 分块最大平均差 < 4.0 视为"无局部变化"（片段类主判据）
    AUDIO_PEAK_MIN_DB = -30.0   # 峰值低于 -30dB 视为太小声

    def _extract_scripts_from_zip(self, template_path: str) -> str:
        """读取模板包内渲染脚本源码（adapter/render/native/assistant），
        用于确定性风险静态扫描。支持 .motion ZIP 与解包目录两种形态。
        返回拼接文本，失败返回空串。"""
        wanted = (".py", ".mjs", ".js", ".html")
        chunks = []

        def _collect_text(path: Path) -> None:
            if path.is_dir():
                for p in path.rglob("*"):
                    try:
                        if p.is_file():
                            _collect_text(p)
                    except Exception:
                        continue
                return
            low = path.name.lower()
            if not low.endswith(wanted):
                return
            # 平台/工具自带运行时（assistant helper、模板共用 _render 基座）
            # 不是作者源码，扫它们只会产生假阳性。
            posix = path.as_posix().lower()
            if any(seg in posix for seg in ("/assistant/", "/_render/",
                                            "/node_modules/", "/__pycache__/")):
                return
            if low == "motion.mjs":
                return
            if any(seg in low for seg in ("node_modules", "__pycache__")):
                return
            try:
                data = path.read_bytes()
                if len(data) > 1_000_000:
                    data = data[:1_000_000]
                chunks.append(f"=== {path.name} ===\n"
                              + data.decode("utf-8", errors="replace"))
            except Exception:
                pass

        import zipfile
        path = Path(template_path)
        if path.is_dir():
            _collect_text(path)
            return "\n".join(chunks)
        try:
            with zipfile.ZipFile(path, "r") as z:
                for name in z.namelist():
                    low = name.lower()
                    if not low.endswith(wanted):
                        continue
                    if any(seg in low for seg in ("assistant/", "_render/",
                                                  "node_modules/", "__pycache__/")):
                        continue
                    if low.endswith("motion.mjs"):
                        continue
                    try:
                        data = z.read(name)
                        if len(data) > 1_000_000:  # 单文件上限 1MB
                            data = data[:1_000_000]
                        chunks.append(f"=== {name} ===\n"
                                      + data.decode("utf-8", errors="replace"))
                    except Exception:
                        continue
                # 个人化包：源码在嵌套 content/release.bin 里，外扫不到时下沉一层
                rel = next((n for n in z.namelist() if n.endswith("release.bin")), None)
                if not chunks and rel:
                    payload = z.read(rel)
                    if payload[:4] == b"PK\x03\x04":
                        import io as _io
                        with zipfile.ZipFile(_io.BytesIO(payload)) as inner:
                            for name in inner.namelist():
                                low = name.lower()
                                if not low.endswith(wanted):
                                    continue
                                if any(seg in low for seg in ("assistant/", "_render/",
                                                              "node_modules/", "__pycache__/")):
                                    continue
                                if low.endswith("motion.mjs"):
                                    continue
                                try:
                                    data = inner.read(name)
                                    if len(data) > 1_000_000:
                                        data = data[:1_000_000]
                                    chunks.append(f"=== {name} ===\n"
                                                  + data.decode("utf-8", errors="replace"))
                                except Exception:
                                    continue
        except Exception:
            return ""
        return "\n".join(chunks)

    def check_determinism_static(self, template_path: str,
                                 params: dict) -> list[CheckResult]:
        """确定性静态检查（渲染前）：源码时钟/随机风险扫描 + 同轨重叠。

        对应 OpenFilm look：把"作者以为自己是纯函数"和"实际是纯函数"
        区分开——命中时钟/随机/from 型 tween 的模板，预览与成片可能
        不一致，需要 P0-b 时钟冻结或模板侧整改。
        """
        results = []
        t0 = time.time()

        # D1: 源码确定性风险扫描
        source = self._extract_scripts_from_zip(template_path)
        if not source:
            results.append(CheckResult("determinism", "clock_risk", CheckStatus.WARN,
                                       "无法读取模板源码，跳过确定性风险扫描"))
        else:
            hits = {}
            for pattern, label in self.CLOCK_RISK_PATTERNS.items():
                idx = source.find(pattern)
                if idx != -1:
                    # 定位命中位置所在行（取前后 60 字符做上下文）
                    line_start = source.rfind("\n", 0, idx) + 1
                    snippet = source[line_start:idx + 80].strip()
                    hits.setdefault(pattern, snippet[:120])
            if hits:
                msgs = []
                for pattern, snippet in hits.items():
                    msgs.append(f"{self.CLOCK_RISK_PATTERNS[pattern]}（{snippet}…）")
                results.append(CheckResult(
                    "determinism", "clock_risk", CheckStatus.WARN,
                    "检测到确定性风险：" + "；".join(msgs)
                    + "。该模板需要 P0-b 时钟冻结或模板侧整改"
                      "（预览与成片可能不一致）"))
            else:
                results.append(CheckResult("determinism", "clock_risk",
                                           CheckStatus.PASS,
                                           "未发现 Date/随机/from 型 tween 依赖"))

        # D2: 同轨片段重叠检查（personal timeline.segments）
        timeline = params.get("timeline") if isinstance(params, dict) else None
        segments = []
        if isinstance(timeline, dict):
            segments = timeline.get("segments") or []
        overlap = self._check_clip_overlap(segments)
        if segments:
            if overlap:
                results.append(CheckResult("determinism", "clip_overlap",
                                           CheckStatus.FAIL,
                                           f"同轨片段重叠: {overlap}"))
            else:
                results.append(CheckResult("determinism", "clip_overlap",
                                           CheckStatus.PASS,
                                           f"{len(segments)} 个片段无重叠"))
        else:
            results.append(CheckResult("determinism", "clip_overlap",
                                       CheckStatus.PASS,
                                       "参数无 timeline.segments（标准格式场景段无时间字段，跳过）"))

        for r in results:
            r.duration_ms = int((time.time() - t0) * 1000)
        return results

    @staticmethod
    def _check_clip_overlap(segments: list) -> str:
        """检查时间线同轨片段是否重叠（OpenFilm: 同轨 clip 不得重叠）。

        segments 形如 [{"id","start","end"}, ...]；start/end 为秒。
        返回首个重叠描述；无重叠返回空串。
        """
        if not segments:
            return ""
        rows = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            s = seg.get("start")
            e = seg.get("end")
            if isinstance(s, (int, float)) and isinstance(e, (int, float)):
                rows.append((float(s), float(e), str(seg.get("id", "?"))))
        rows.sort(key=lambda r: (r[0], r[1]))
        for i in range(1, len(rows)):
            ps, pe, pid = rows[i - 1]
            cs, ce, cid = rows[i]
            if cs < pe - 1e-6:
                return (f"{pid}[{ps:.3f},{pe:.3f}] 与 {cid}[{cs:.3f},{ce:.3f}]"
                        f" 重叠 {pe - cs:.3f}s")
        return ""

    def _ffmpeg_extract_frames(self, output_path: str, n: int = 12
                               ) -> tuple[list, Optional[dict]]:
        """用 ffmpeg 从成片抽 n 帧 raw RGB24，返回 (frames, meta)。

        meta: {width, height, duration}；失败返回 ([], None)。
        """
        probe = _ffprobe(output_path)
        if not probe:
            return [], None
        video = next((s for s in probe.get("streams", [])
                      if s.get("codec_type") == "video"), None)
        if not video:
            return [], None
        width = int(video.get("width", 0))
        height = int(video.get("height", 0))
        duration = float(probe.get("format", {}).get("duration", 0))
        if width <= 0 or height <= 0 or duration <= 0:
            return [], None
        fps = 30.0
        fr = video.get("r_frame_rate", "")
        if "/" in fr:
            try:
                num, den = fr.split("/")
                fps = float(num) / max(1.0, float(den))
            except Exception:
                fps = 30.0
        step = max(1, int(duration * fps / n))
        # 注意：ffmpeg 7.x 已移除 -vsync（改用 -fps_mode），这里两者都不用；
        # -frames:v 限制输出帧数，避免长视频把全帧 raw 灌进内存。
        cmd = ["ffmpeg", "-v", "error", "-i", output_path,
               "-vf", f"select='not(mod(n,{step}))'", "-frames:v", str(n),
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-an", "-"]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=120)
            if proc.returncode != 0:
                return [], None
        except Exception:
            return [], None
        frame_bytes = width * height * 3
        raw = proc.stdout
        frames = [raw[i:i + frame_bytes]
                  for i in range(0, len(raw) - frame_bytes + 1, frame_bytes)]
        # 最多返回 n 帧
        frames = frames[:n]
        if not frames:
            return [], None
        return frames, {"width": width, "height": height, "duration": duration}

    @staticmethod
    def _frame_stats(frame: bytes, stride: int = 16) -> dict:
        """抽样像素计算 0-255 尺度方差与均值（纯 Python，无 numpy 依赖）。"""
        n = len(frame)
        if n == 0:
            return {"mean": 0.0, "variance": 0.0}
        samples = frame[::stride]
        if not samples:
            samples = frame[:1]
        total = sum(samples)
        count = len(samples)
        mean = total / count
        var = sum((v - mean) ** 2 for v in samples) / count
        return {"mean": mean, "variance": var}

    @staticmethod
    def _frame_pixel_diff(a: bytes, b: bytes, stride: int = 16) -> float:
        """同位置抽样像素的平均绝对差（0-255 尺度）。

        与 Δmean 的区别：Δmean 只是全局亮度的变化，透明底/纯色底片段里
        主体移动几乎不改变全局均值；逐像素差能反映空间上的位移与显隐。
        """
        if not a or not b:
            return 0.0
        n = min(len(a), len(b))
        samples_a = a[:n:stride]
        samples_b = b[:n:stride]
        if not samples_a:
            return 0.0
        total = 0
        for va, vb in zip(samples_a, samples_b):
            total += abs(va - vb)
        return total / len(samples_a)

    @staticmethod
    def _frame_block_diff(a: bytes, b: bytes, width: int, height: int,
                          grid: tuple[int, int] = (16, 9),
                          stride: int = 16) -> float:
        """分块比较：返回"最活跃块"的平均绝对差（0-255 尺度）。

        片段类模板（整屏透明/纯色底、主体只占很小面积）用全局均值 Δmean
        判"是否有动效"会一直误报；按 16×9 网格分块后取最大块差值，
        能捕捉"局部主体在动"，同时不被大面积静背景稀释。
        """
        if not a or not b or width <= 0 or height <= 0:
            return 0.0
        cols, rows = grid
        n = min(len(a), len(b))
        frame_bytes = width * height * 3
        if n < frame_bytes:
            return 0.0
        sums = [0] * (cols * rows)
        counts = [0] * (cols * rows)
        px = frame_bytes // stride * stride  # 按 stride 网格取样
        for base in range(0, px, stride * 3):
            for c in range(3):
                idx = base + c
                if idx >= n:
                    break
                pixel = base // 3
                y = pixel // width
                x = pixel % width
                blk = min(rows - 1, y * rows // height) * cols + min(cols - 1, x * cols // width)
                sums[blk] += abs(a[idx] - b[idx])
                counts[blk] += 1
        diffs = [sums[i] / counts[i] for i in range(len(sums)) if counts[i]]
        return max(diffs) if diffs else 0.0

    def check_output_frames(self, output_path: str,
                            n: int = 12) -> list[CheckResult]:
        """输出级画面确定性检查（渲染后，ffmpeg 抽帧）：
        flat_frame（镜头中点纯色）+ frame_static（整片静止无动效）。
        """
        results = []
        t0 = time.time()
        frames, meta = self._ffmpeg_extract_frames(output_path, n)
        if not frames or not meta:
            results.append(CheckResult("output", "frame_extract", CheckStatus.WARN,
                                       "无法抽帧（ffmpeg 不可用或输出异常），"
                                       "跳过画面确定性检查"))
            return results

        stats = [self._frame_stats(f) for f in frames]
        flat = [i for i, s in enumerate(stats) if s["variance"] < self.FLAT_FRAME_VARIANCE]
        if len(flat) == n:
            results.append(CheckResult("output", "flat_frame", CheckStatus.FAIL,
                                       "全部采样帧方差 < 6，画面接近纯色/未渲染"))
        elif flat:
            results.append(CheckResult("output", "flat_frame", CheckStatus.WARN,
                                       f"{len(flat)}/{n} 帧画面接近纯色"
                                       f"（方差<{self.FLAT_FRAME_VARIANCE:.0f}）: "
                                       f"帧位 {flat}"))
        else:
            results.append(CheckResult("output", "flat_frame", CheckStatus.PASS,
                                       f"{n} 帧方差均 ≥ {self.FLAT_FRAME_VARIANCE:.0f}"))

        # 整片静止：全局亮度 Δmean 与逐像素 Δpx 两个口径都低才算"无动效"。
        # 片段类模板（整屏透明/纯色底、主体面积小）Δmean 天然接近 0，
        # 只看 Δmean 会误报，故以逐像素差作为第二判据（任一达标即 PASS）。
        first = stats[0]
        mad = max(abs(s["mean"] - first["mean"]) for s in stats[1:]) if len(stats) > 1 else 0.0
        w, h = meta.get("width", 0), meta.get("height", 0)
        px_diff = (max(self._frame_pixel_diff(frames[0], f) for f in frames[1:])
                   if len(frames) > 1 else 0.0)
        blk_diff = (max(self._frame_block_diff(frames[0], f, w, h) for f in frames[1:])
                    if len(frames) > 1 else 0.0)
        if (mad < self.STATIC_FRAME_MAD and px_diff < self.STATIC_FRAME_PIXEL_DIFF
                and blk_diff < self.STATIC_FRAME_BLOCK_DIFF):
            results.append(CheckResult("output", "frame_static", CheckStatus.WARN,
                                       f"采样帧画面几乎无变化 (Δmean={mad:.2f}, "
                                       f"Δpx={px_diff:.2f}, Δblk={blk_diff:.2f})，"
                                       "模板可能没有随时间变化的动效"))
        else:
            results.append(CheckResult("output", "frame_static", CheckStatus.PASS,
                                       f"画面随时间变化正常 (Δmean={mad:.2f}, "
                                       f"Δpx={px_diff:.2f}, Δblk={blk_diff:.2f})"))

        for r in results:
            r.duration_ms = int((time.time() - t0) * 1000)
        return results

    def check_output_audio(self, output_path: str) -> list[CheckResult]:
        """输出级音频检查（渲染后）：峰值太小声 + LUFS（pyloudnorm 可选）。"""
        results = []
        t0 = time.time()
        cmd = ["ffmpeg", "-v", "info", "-i", output_path, "-af", "volumedetect",
               "-vn", "-sn", "-f", "null", "-"]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=120)
        except Exception:
            proc = None
        if proc is None or proc.returncode != 0:
            # 失败时用 ffprobe 复查：无音轨与"音量读取失败"是两回事，
            # 把前者说成后者会掩盖真实原因
            probe = _ffprobe(output_path) or {}
            has_audio = any(s.get("codec_type") == "audio"
                            for s in probe.get("streams", []))
            msg = ("无法读取音频音量（ffmpeg volumedetect 失败）" if has_audio
                   else "无音轨（成片未含音频）")
            results.append(CheckResult("output", "audio_peak", CheckStatus.WARN, msg))
            for r in results:
                r.duration_ms = int((time.time() - t0) * 1000)
            return results

        text = (proc.stderr or b"").decode("utf-8", errors="replace")
        mean_db = max_db = None
        for line in text.splitlines():
            line = line.strip()
            # ffmpeg 把统计打在带前缀的日志行里，如
            # "[Parsed_volumedetect_0 @ 0x...] mean_volume: -32.0 dB"
            if "mean_volume:" in line:
                try:
                    mean_db = float(line.split("mean_volume:")[1].strip().split()[0])
                except Exception:
                    pass
            elif "max_volume:" in line:
                try:
                    max_db = float(line.split("max_volume:")[1].strip().split()[0])
                except Exception:
                    pass

        if max_db is None:
            results.append(CheckResult("output", "audio_peak", CheckStatus.WARN,
                                       "未解析到音量数据（可能无音轨）"))
        elif max_db < self.AUDIO_PEAK_MIN_DB:
            results.append(CheckResult("output", "audio_peak", CheckStatus.WARN,
                                       f"峰值 {max_db:.1f}dB < -30dB，太小声听不见"))
        else:
            results.append(CheckResult("output", "audio_peak", CheckStatus.PASS,
                                       f"峰值 {max_db:.1f}dB / 均值 {mean_db or 0:.1f}dB"))

        # LUFS（pyloudnorm 可选；缺失时 WARN 不失败）
        try:
            import soundfile as sf
            import pyloudnorm as pyln
            import tempfile
            data = rate = None
            # libsndfile 读不了 mp4/aac：先 ffmpeg 解成 48k 单声道 wav 再算
            with tempfile.TemporaryDirectory() as tmp:
                wav = Path(tmp) / "audio.wav"
                dec = subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-i", output_path,
                     "-vn", "-ac", "1", "-ar", "48000", str(wav)],
                    capture_output=True, timeout=180,
                )
                if dec.returncode == 0 and wav.is_file():
                    data, rate = sf.read(str(wav), dtype="float32")
            if data is None:
                # 解码不可用时退回直读（wav 等 libsndfile 原生支持的容器）
                data, rate = sf.read(output_path, dtype="float32")
            meter = pyln.Meter(rate)
            lufs = meter.integrated_loudness(data)
            results.append(CheckResult("output", "loudness_lufs", CheckStatus.PASS,
                                       f"整片 LUFS {lufs:.1f}"))
        except ImportError:
            results.append(CheckResult("output", "loudness_lufs", CheckStatus.WARN,
                                       "pyloudnorm/soundfile 未安装，跳过 LUFS（可选增强）"))
        except Exception as e:
            results.append(CheckResult("output", "loudness_lufs", CheckStatus.WARN,
                                       f"LUFS 计算失败: {e}"))

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

        # Determinism (P1): 静态风险扫描 + 同轨重叠（渲染前）
        all_checks.extend(self.check_determinism_static(template_path, params))

        # Layer 3: only if render_result provided
        if render_result is not None:
            all_checks.extend(self.check_render(render_result))

        # Layer 4: only if output_path provided
        if output_path is not None:
            all_checks.extend(self.check_output(output_path, expected))
            all_checks.extend(self.check_output_stream(output_path, expected))
            all_checks.extend(self.check_output_frames(output_path))
            all_checks.extend(self.check_output_audio(output_path))

        report.checks = all_checks

        # Determine overall status
        if any(c.status == CheckStatus.FAIL for c in all_checks):
            report.status = CheckStatus.FAIL
        elif any(c.status == CheckStatus.WARN for c in all_checks):
            report.status = CheckStatus.WARN
        else:
            report.status = CheckStatus.PASS

        return report
