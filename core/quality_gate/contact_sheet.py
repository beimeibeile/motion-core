"""Contact sheet 生成：12 帧 + 波形 + 秒刻度，让 AI/人"看见"节奏。

对应 OpenFilm 借鉴 P3「contact sheet（12 帧 + 波形 + 秒刻度）」：
可直接加进质量报告（O2 验收纪律：stills 抽查 + 声画验收的自然落点）。

输出一张 PNG：
- 顶部标题（文件名 + 分辨率 + 时长）
- 帧网格：n 帧（默认 12），每帧下方标注时间戳
- 波形条：ffmpeg showwaves（声道峰值包络）
- 秒刻度条：0 / N/3 / 2N/3 / N 秒 + 刻度线

依赖：ffmpeg（抽帧/波形）+ Pillow（合成）；两者缺失时逐项降级。
"""

import json
import os
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

FRAME_THUMB_W = 320      # 帧缩略图宽度
FRAME_THUMB_H = 180      # 帧缩略图高度（16:9 基准）
GRID_COLS = 4            # 帧网格列数
WAVE_H = 80              # 波形条高度
SCALE_H = 26             # 秒刻度条高度
GRID_GAP = 10            # 网格间距
PAD = 16                 # 画布边距
TITLE_H = 40             # 标题区高度

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",      # 微软雅黑（中文）
    r"C:\Windows\Fonts\arial.ttf",
]


def _find_font(size: int) -> Optional[ImageFont.ImageFont]:
    for p in _FONT_CANDIDATES:
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return None


def probe_media(path: str) -> Optional[Dict]:
    """ffprobe 元数据：{width, height, duration, has_audio}。"""
    cmd = ["ffprobe", "-v", "error", "-show_format", "-show_streams",
           "-of", "json", path]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
        if proc.returncode != 0:
            return None
        info = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except Exception:
        return None
    video = next((s for s in info.get("streams", [])
                  if s.get("codec_type") == "video"), None)
    if not video:
        return None
    duration = float(info.get("format", {}).get("duration", 0) or 0)
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    return {
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
        "duration": duration,
        "has_audio": has_audio,
    }


def extract_frames(path: str, n: int = 12,
                   thumb_w: int = FRAME_THUMB_W) -> List[Image.Image]:
    """ffmpeg 均匀抽 n 帧，缩放为 thumb_w 宽的 RGB 帧图。"""
    meta = probe_media(path)
    if not meta or meta["duration"] <= 0:
        return []
    duration = meta["duration"]
    thumbs: List[Image.Image] = []
    for i in range(n):
        t = duration * i / max(1, n - 1)
        cmd = ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", path,
               "-frames:v", "1", "-vf", f"scale={thumb_w}:-2",
               "-f", "image2pipe", "-vcodec", "png", "-"]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=60)
            if proc.returncode != 0 or not proc.stdout:
                continue
            img = tempfile.SpooledTemporaryFile(
                max_size=8 * 1024 * 1024, mode="w+b",
                prefix="cs_", suffix=".png")
            img.write(proc.stdout)
            img.seek(0)
            thumbs.append(Image.open(img).convert("RGB"))
        except Exception:
            continue
    return thumbs


def waveform_image(path: str, width: int, height: int) -> Optional[Image.Image]:
    """ffmpeg showwaves 生成波形 PNG（声道峰值包络）。"""
    cmd = ["ffmpeg", "-v", "error", "-i", path,
           "-filter_complex",
           f"showwaves=s={width}x{height}:mode=cline:colors=#4a7dff",
           "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
        if proc.returncode != 0 or not proc.stdout:
            return None
        buf = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024,
                                            mode="w+b", prefix="cs_", suffix=".png")
        buf.write(proc.stdout)
        buf.seek(0)
        return Image.open(buf).convert("RGB")
    except Exception:
        return None


def _format_ts(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def compose_contact_sheet(frames: List[Image.Image],
                          wave: Optional[Image.Image],
                          meta: Dict, title: str = "") -> Image.Image:
    """帧网格 + 波形 + 秒刻度 合成一张 contact sheet。"""
    n = len(frames)
    if n == 0:
        n = 1
    cols = min(GRID_COLS, n)
    rows = (n + cols - 1) // cols
    fw, fh = frames[0].size if frames else (FRAME_THUMB_W, FRAME_THUMB_H)
    fh = int(fw * 9 / 16) if frames else fh
    grid_w = cols * fw + (cols - 1) * GRID_GAP
    wave_w = grid_w
    canvas_w = max(grid_w, wave_w) + 2 * PAD
    canvas_h = (TITLE_H + PAD + rows * (fh + 22 + GRID_GAP)
                + WAVE_H + 8 + SCALE_H + PAD + PAD)
    canvas = Image.new("RGB", (canvas_w, canvas_h), "#1e1e2e")
    draw = ImageDraw.Draw(canvas)
    font_title = _find_font(18)
    font_small = _find_font(12)

    # 标题
    if font_title:
        head = title or (os.path.basename(meta.get("path", ""))
                         if meta.get("path") else "contact sheet")
        info = (f"{meta.get('width', '?')}×{meta.get('height', '?')} "
                f"· {meta.get('duration', 0):.1f}s"
                + (" · 有音轨" if meta.get("has_audio") else ""))
        draw.text((PAD, 10), head, fill="#e6e6e6", font=font_title)
        draw.text((PAD, 32), info, fill="#9aa4b0", font=font_small)

    # 帧网格
    for i, img in enumerate(frames[:n]):
        x = PAD + (i % cols) * (fw + GRID_GAP)
        y = TITLE_H + PAD + (i // cols) * (fh + 22 + GRID_GAP)
        canvas.paste(img, (x, y))
        draw.rectangle([x, y, x + fw, y + fh], outline="#3a3f4b", width=1)
        if font_small:
            t = meta["duration"] * i / max(1, n - 1)
            draw.text((x, y + fh + 3), f"t={_format_ts(t)}",
                      fill="#c8d0d8", font=font_small)

    wave_y = TITLE_H + PAD + rows * (fh + 22 + GRID_GAP) + GRID_GAP
    if wave:
        canvas.paste(wave, (PAD, wave_y))
    else:
        draw.rectangle([PAD, wave_y, PAD + wave_w, wave_y + WAVE_H],
                       fill="#2a2e3a", outline="#3a3f4b")
        if font_small:
            draw.text((PAD + 8, wave_y + WAVE_H // 2 - 8),
                      "（无波形：无音轨或 ffmpeg 波形失败）",
                      fill="#9aa4b0", font=font_small)

    # 秒刻度
    scale_y = wave_y + WAVE_H + 8
    duration = float(meta.get("duration", 0) or 0)
    draw.rectangle([PAD, scale_y, PAD + wave_w, scale_y + SCALE_H],
                   fill="#242734", outline="#3a3f4b")
    draw.line([PAD, scale_y, PAD + wave_w, scale_y], fill="#5a6270")
    if duration > 0 and font_small:
        for frac in (0.0, 1 / 3, 2 / 3, 1.0):
            x = PAD + int(wave_w * frac)
            draw.line([x, scale_y, x, scale_y + 5], fill="#8a94a2")
            draw.text((x + 2, scale_y + 6),
                      f"{_format_ts(duration * frac)}s",
                      fill="#c8d0d8", font=font_small)
    return canvas


def generate_contact_sheet(output_path: str, out_path: str,
                           n: int = 12) -> Dict:
    """从成片生成 contact sheet PNG。返回结果 dict（失败含 error 字段）。"""
    meta = probe_media(output_path)
    if not meta:
        return {"error": "无法探测视频（ffprobe 失败或输入非视频）",
                "output": out_path}
    meta["path"] = output_path
    frames = extract_frames(output_path, n)
    wave = waveform_image(output_path, FRAME_THUMB_W * GRID_COLS, WAVE_H)
    canvas = compose_contact_sheet(
        frames, wave, meta,
        title=f"{os.path.basename(output_path)} · contact sheet")
    canvas.save(out_path, "PNG")
    return {
        "output": out_path,
        "width": canvas.width,
        "height": canvas.height,
        "frames": len(frames),
        "video": f"{meta['width']}x{meta['height']}",
        "duration": round(meta["duration"], 2),
        "has_audio": meta["has_audio"],
        "waveform": wave is not None,
    }
