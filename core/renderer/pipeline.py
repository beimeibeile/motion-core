"""End-to-end render pipeline: detect format -> select engine -> render -> verify output."""

import json
import subprocess
import uuid
from pathlib import Path
from typing import Callable, Optional

from ..template_parser import detect_format, parse_template
from .engine import StandardRenderEngine, PersonalRenderEngine, RenderError
from .embedded_adapter import EmbeddedAdapterEngine, has_embedded_adapter


class RenderPipeline:
    """Complete render pipeline with auto format detection and output verification.

    Usage:
        pipeline = RenderPipeline("template.motion")
        result = pipeline.render(params={"title": "Hello"}, output_path="out.mp4")
    """

    def __init__(self, template_path: str, work_dir: str = None,
                 instance_tag: str = None):
        """instance_tag: 渲染实例目录后缀。

        默认随机生成。同一模板被多个任务并发渲染时，若共用 `_render_work/<模板名>`
        会互相解包/清理同一目录（WinError 145 目录非空、WinError 5 拒绝访问），
        因此每个渲染实例必须独占一个工作目录。
        """
        self.template_path = Path(template_path).resolve()
        self.work_dir = Path(work_dir) if work_dir else self.template_path.parent / "_render_work"
        self.instance_tag = instance_tag or uuid.uuid4().hex[:8]
        self.format = detect_format(template_path)
        self.template_info = parse_template(template_path)

    def render(self, params: dict, output_path: str,
               progress_cb: Callable = None,
               cleanup: bool = True) -> dict:
        """Render template with params to output_path.

        Args:
            params: Template parameters dict (must match params.schema.json)
            output_path: Output MP4 path
            progress_cb: Optional callback(stage, percent, message)
            cleanup: Whether to clean up work directory after success

        Returns:
            dict with keys: success, output_path, format, width, height, fps,
                           duration_s, elapsed_s, error (if failed)
        """
        # 每个渲染实例独占目录（并发安全）
        instance_dir = self.work_dir / f"{self.template_path.stem}-{self.instance_tag}"
        instance_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Select engine by format
            if self.format == 'standard':
                engine = StandardRenderEngine(str(self.template_path), str(instance_dir))
            elif self.format == 'personal-download-v1':
                # Packages with an embedded adapter contract bypass the helper's
                # fixed dispatch chain and call their adapter directly.
                if has_embedded_adapter(str(self.template_path)):
                    engine = EmbeddedAdapterEngine(str(self.template_path), str(instance_dir))
                else:
                    engine = PersonalRenderEngine(str(self.template_path), str(instance_dir))
            else:
                raise RenderError("env", f"不支持的模板格式: {self.format}")

            # Render
            result = engine.render(params, output_path, progress_cb)

            # Verify output with ffprobe
            meta = self._verify_output(output_path)
            result.update(meta)

            # Cleanup
            if cleanup and result.get('success'):
                try:
                    import shutil
                    shutil.rmtree(instance_dir, ignore_errors=True)
                except Exception:
                    pass

            return result

        except RenderError as e:
            return {
                'success': False,
                'output_path': output_path,
                'format': self.format,
                'error_stage': e.stage,
                'error': e.message,
                'error_detail': e.detail,
            }
        except Exception as e:
            return {
                'success': False,
                'output_path': output_path,
                'format': self.format,
                'error_stage': 'unknown',
                'error': str(e),
            }

    @staticmethod
    def _verify_output(output_path: str) -> dict:
        """Verify output video with ffprobe. Returns metadata dict."""
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries',
                 'stream=codec_name,width,height,r_frame_rate,duration',
                 '-show_entries', 'format=duration',
                 '-of', 'json', output_path],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                streams = data.get('streams', [])
                video = next((s for s in streams if s.get('codec_name') in ('h264', 'hevc', 'vp9')), None)
                if video:
                    fps_str = video.get('r_frame_rate', '0/1')
                    num, den = fps_str.split('/')
                    fps = float(num) / float(den) if float(den) != 0 else 0
                    return {
                        'width': int(video.get('width', 0)),
                        'height': int(video.get('height', 0)),
                        'fps': round(fps, 2),
                        'duration_s': round(float(data.get('format', {}).get('duration', 0)), 2),
                        'codec': video.get('codec_name', ''),
                        'verified': True,
                    }
            return {'verified': False}
        except Exception:
            return {'verified': False}
