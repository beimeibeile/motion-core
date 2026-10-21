"""Personal download v1 parser (AES-256-GCM encrypted)."""

from .base import TemplateParser, TemplateInfo


class PersonalParser(TemplateParser):
    """Parser for personal-download-v1 format (outer 1.0.0-rc.1).

    Contains:
    - motion.json (outer manifest)
    - content/release.bin (AES-256-GCM encrypted content)
    - personal/motiongit.key.json (downloader's API Key - DO NOT EXTRACT)
    - assistant/motion.mjs (runtime helper)
    """

    def parse(self) -> TemplateInfo:
        # Implementation in Phase 0
        raise NotImplementedError
