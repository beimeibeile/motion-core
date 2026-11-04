"""P3 contact sheet：帧 + 波形 + 秒刻度 合成测试。

- 纯 PIL 合成路径（无 ffmpeg 依赖）：compose_contact_sheet
- 集成路径（ffmpeg lavfi 生成 2s 测试视频）：generate_contact_sheet
"""

import os
import subprocess
import tempfile

import pytest
from PIL import Image

from core.quality_gate.contact_sheet import (compose_contact_sheet,
                                             extract_frames,
                                             generate_contact_sheet,
                                             probe_media, waveform_image)


def _solid_frame(w=320, h=180, color=(40, 80, 200)) -> Image.Image:
    img = Image.new("RGB", (w, h), color)
    return img


def test_probe_media_lavfi():
    """ffmpeg lavfi 生成 2s 视频，probe 应读出元数据。"""
    with tempfile.TemporaryDirectory() as d:
        vid = os.path.join(d, "t.mp4")
        cmd = ["ffmpeg", "-v", "error", "-f", "lavfi",
               "-i", "testsrc2=size=320x180:rate=15:duration=2",
               "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
               "-y", vid]
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
        assert proc.returncode == 0, proc.stderr.decode()
        meta = probe_media(vid)
        assert meta is not None
        assert meta["width"] == 320
        assert meta["height"] == 180
        assert 1.5 <= meta["duration"] <= 2.5
        assert meta["has_audio"] is True


def test_extract_frames_lavfi():
    with tempfile.TemporaryDirectory() as d:
        vid = os.path.join(d, "t.mp4")
        cmd = ["ffmpeg", "-v", "error", "-f", "lavfi",
               "-i", "testsrc2=size=320x180:rate=15:duration=2",
               "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-y", vid]
        subprocess.run(cmd, capture_output=True, timeout=60)
        frames = extract_frames(vid, n=4)
        assert 1 <= len(frames) <= 4
        for f in frames:
            assert f.size[0] == 320


def test_waveform_lavfi():
    with tempfile.TemporaryDirectory() as d:
        vid = os.path.join(d, "t.mp4")
        cmd = ["ffmpeg", "-v", "error", "-f", "lavfi",
               "-i", "testsrc2=size=320x180:rate=15:duration=2",
               "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
               "-y", vid]
        subprocess.run(cmd, capture_output=True, timeout=60)
        wave = waveform_image(vid, 320, 80)
        assert wave is not None
        assert wave.size == (320, 80)


def test_compose_contact_sheet_pure():
    """纯 PIL 合成：4 帧 + 无波形，断言画布尺寸与帧网格。"""
    frames = [_solid_frame() for _ in range(4)]
    canvas = compose_contact_sheet(
        frames, None,
        {"width": 320, "height": 180, "duration": 8.0, "has_audio": False})
    assert canvas.width > 320 * 2
    assert canvas.height > 180
    # 帧区应有彩色像素（非纯背景）
    pixels = canvas.getpixel((20, 60))
    assert pixels == (40, 80, 200)


def test_compose_contact_sheet_empty_frames():
    canvas = compose_contact_sheet(
        [], None,
        {"width": 320, "height": 180, "duration": 0.0, "has_audio": False})
    assert canvas.width > 0
    assert canvas.height > 0


def test_generate_contact_sheet_integration():
    """端到端：lavfi 视频 → PNG 落盘 + 结果字段。"""
    with tempfile.TemporaryDirectory() as d:
        vid = os.path.join(d, "t.mp4")
        cmd = ["ffmpeg", "-v", "error", "-f", "lavfi",
               "-i", "testsrc2=size=320x180:rate=15:duration=2",
               "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
               "-y", vid]
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
        assert proc.returncode == 0
        out = os.path.join(d, "sheet.png")
        result = generate_contact_sheet(vid, out, n=6)
        assert "error" not in result
        assert os.path.isfile(out)
        with Image.open(out) as img:
            assert img.width > 0 and img.height > 0
        # 末尾帧（t≈duration）可能超视频实际时长而缺失，允许 4..6
        assert 4 <= result["frames"] <= 6
        assert result["has_audio"] is True

def test_generate_contact_sheet_not_video():
    with tempfile.TemporaryDirectory() as d:
        fake = os.path.join(d, "x.mp4")
        with open(fake, "wb") as f:
            f.write(b"not a video")
        result = generate_contact_sheet(fake, os.path.join(d, "s.png"))
        assert "error" in result
