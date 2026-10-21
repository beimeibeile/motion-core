"""JSON Schema parameter definition parser and validator."""


class ParamSchema:
    """Parse and validate template parameters against params.schema.json."""

    def __init__(self, schema: dict):
        self.schema = schema

    def validate(self, params: dict) -> list[str]:
        """Validate params against schema. Returns list of error messages."""
        raise NotImplementedError

    def get_defaults(self) -> dict:
        """Extract default values from schema."""
        raise NotImplementedError

    def describe(self) -> dict:
        """Return human-readable parameter description."""
        raise NotImplementedError
