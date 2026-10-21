"""Standard format parser (plain text motion.json)."""

from .base import TemplateParser, TemplateInfo


class StandardParser(TemplateParser):
    """Parser for standard .motion format (specVersion 0.1.0-draft.1)."""

    def parse(self) -> TemplateInfo:
        # Implementation in Phase 0
        raise NotImplementedError
