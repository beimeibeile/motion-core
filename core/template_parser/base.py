"""Base classes for template parsing."""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TemplateInfo:
    """Parsed template metadata and structure."""
    path: str
    format: str  # "standard" or "personal-download-v1"
    spec_version: str
    template_id: str
    template_version: str
    name: str
    runtime_profile: str
    runtime_entry: str
    output_width: int
    output_height: int
    output_fps: float
    duration_policy: str
    duration_seconds: Optional[float] = None
    parameters_schema: Optional[dict] = None
    guide: Optional[dict] = None
    assets: list = field(default_factory=list)
    licenses: list = field(default_factory=list)
    raw_motion_json: Optional[dict] = None


class TemplateParser:
    """Base class for .motion template parsers."""

    def __init__(self, path: str):
        self.path = path

    def parse(self) -> TemplateInfo:
        """Parse the template and return TemplateInfo."""
        raise NotImplementedError

    def validate(self) -> list[str]:
        """Validate template structure. Returns list of errors."""
        raise NotImplementedError
