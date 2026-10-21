"""Preset management - save and load parameter combinations."""


class PresetManager:
    """Manage template parameter presets."""

    def __init__(self, presets_dir: str):
        self.presets_dir = presets_dir

    def list(self) -> list[str]:
        """List available preset names."""
        raise NotImplementedError

    def load(self, name: str) -> dict:
        """Load a preset by name."""
        raise NotImplementedError

    def save(self, name: str, params: dict) -> None:
        """Save params as a named preset."""
        raise NotImplementedError
