"""
Template Parser - Parse .motion template packages.

Supports three formats:
- standard: Plain text motion.json with inline content (阿杜早期个人项目规范)
- personal-download-v1: AES-256-GCM encrypted content bundle with personal API Key (MotionGit 平台分发)
"""

import zipfile
from pathlib import Path

from .base import TemplateParser, TemplateInfo
from .standard import StandardParser
from .personal import PersonalParser

__all__ = ["TemplateParser", "TemplateInfo", "StandardParser", "PersonalParser", "parse_template", "detect_format"]


def detect_format(path: str) -> str:
    """Auto-detect .motion template format.

    Detection logic:
    - If ZIP contains 'personal/motiongit.key.json' -> personal-download-v1
    - Otherwise -> standard

    Args:
        path: Path to .motion file

    Returns:
        Format string: 'standard' or 'personal-download-v1'
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Template not found: {path}")

    with zipfile.ZipFile(p, 'r') as z:
        names = z.namelist()
        if 'personal/motiongit.key.json' in names:
            return 'personal-download-v1'
        return 'standard'


def parse_template(path: str) -> TemplateInfo:
    """Auto-detect format and parse a .motion template.

    Args:
        path: Path to .motion file (ZIP container)

    Returns:
        TemplateInfo with parsed metadata, parameters, and runtime info

    Raises:
        FileNotFoundError: if template file doesn't exist
        ValueError: if template format is invalid
    """
    fmt = detect_format(path)
    if fmt == 'standard':
        parser = StandardParser(path)
    elif fmt == 'personal-download-v1':
        parser = PersonalParser(path)
    else:
        raise ValueError(f"Unknown template format: {fmt}")
    return parser.parse()
