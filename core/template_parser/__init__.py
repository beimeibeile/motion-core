"""
Template Parser - Parse .motion template packages.

Supports two formats:
- standard: Plain text motion.json with inline content (specVersion 0.1.0-draft.1)
- personal-download-v1: AES-256-GCM encrypted content bundle with personal API Key (outer 1.0.0-rc.1)
"""

from .base import TemplateParser, TemplateInfo
from .standard import StandardParser
from .personal import PersonalParser

__all__ = ["TemplateParser", "TemplateInfo", "StandardParser", "PersonalParser", "parse_template"]


def parse_template(path: str) -> TemplateInfo:
    """Auto-detect format and parse a .motion template.

    Args:
        path: Path to .motion file (ZIP container)

    Returns:
        TemplateInfo with parsed metadata, parameters, and runtime info
    """
    # Auto-detection logic will be implemented in Phase 0
    raise NotImplementedError("parse_template will be implemented in Phase 0")
