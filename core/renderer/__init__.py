"""Renderer - HTML Canvas frame rendering + FFmpeg encoding + audio synthesis.

Auto-detects template format and selects the appropriate render backend:
- standard: adapter.py (uv run) -> render.mjs -> mix.py
- personal-download-v1 (v1 runtime): motion.mjs run --workflow motion-kit
- personal-download-v1 (v2 runtime): NOT YET SUPPORTED (Windows symlink bug)
"""

from .engine import RenderEngine, StandardRenderEngine, PersonalRenderEngine, RenderError
from .pipeline import RenderPipeline

__all__ = [
    "RenderEngine", "StandardRenderEngine", "PersonalRenderEngine",
    "RenderError", "RenderPipeline",
]
