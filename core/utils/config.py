"""Configuration management."""


class Config:
    """Project configuration with YAML file and environment variable support."""

    def __init__(self, config_path: str = None):
        self.config_path = config_path
        self._data = {}

    def get(self, key: str, default=None):
        """Get config value by dot-separated key."""
        raise NotImplementedError

    def set(self, key: str, value):
        """Set config value."""
        raise NotImplementedError
