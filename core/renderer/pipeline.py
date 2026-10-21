"""End-to-end render pipeline: prepare -> render -> encode -> quality check."""


class RenderPipeline:
    """Complete render pipeline with quality gate integration."""

    def __init__(self, template_path: str):
        self.template_path = template_path

    def render(self, params: dict, output_path: str, **kwargs) -> dict:
        """Render template with params to output_path.

        Returns:
            dict with keys: success, output_path, duration, frames, quality_report
        """
        raise NotImplementedError
