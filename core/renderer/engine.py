"""Render engine using Playwright + Chrome for HTML Canvas frame capture."""


class RenderEngine:
    """Frame-by-frame rendering engine for html-canvas/1 profile templates."""

    def __init__(self, template_path: str, instance_dir: str):
        self.template_path = template_path
        self.instance_dir = instance_dir

    def prepare(self) -> None:
        """Extract template, load dependencies, prepare instance."""
        raise NotImplementedError

    def render_frame(self, frame: int, inputs: dict) -> bytes:
        """Render a single frame and return PNG bytes."""
        raise NotImplementedError

    def render_all(self, inputs: dict, output_dir: str) -> list[str]:
        """Render all frames to output_dir. Returns list of frame file paths."""
        raise NotImplementedError
