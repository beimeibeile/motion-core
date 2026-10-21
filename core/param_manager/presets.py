"""Preset management - save and load parameter combinations."""

import json
import time
from pathlib import Path
from typing import Optional


class PresetManager:
    """Manage template parameter presets.

    Presets are stored as JSON files in a presets directory, organized
    by template ID to avoid name collisions across templates.
    """

    def __init__(self, presets_dir: str):
        self.presets_dir = Path(presets_dir)
        self.presets_dir.mkdir(parents=True, exist_ok=True)

    def _preset_path(self, template_id: str, name: str) -> Path:
        """Get the file path for a preset."""
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in template_id)
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        return self.presets_dir / safe_id / f"{safe_name}.json"

    def list(self, template_id: Optional[str] = None) -> list[str]:
        """List available preset names.

        Args:
            template_id: If provided, list only presets for this template.
                         If None, list all presets (as template_id/name).
        """
        if template_id:
            safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in template_id)
            template_dir = self.presets_dir / safe_id
            if not template_dir.exists():
                return []
            return [p.stem for p in template_dir.glob("*.json")]

        # List all presets across templates
        result = []
        for template_dir in self.presets_dir.iterdir():
            if template_dir.is_dir():
                for p in template_dir.glob("*.json"):
                    result.append(f"{template_dir.name}/{p.stem}")
        return sorted(result)

    def load(self, template_id: str, name: str) -> Optional[dict]:
        """Load a preset by template ID and name.

        Returns None if preset not found.
        """
        path = self._preset_path(template_id, name)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("params", data)  # support both wrapped and raw
        except Exception:
            return None

    def save(self, template_id: str, name: str, params: dict,
             description: str = "") -> None:
        """Save params as a named preset."""
        path = self._preset_path(template_id, name)
        path.parent.mkdir(parents=True, exist_ok=True)

        preset = {
            "name": name,
            "template_id": template_id,
            "description": description,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "params": params,
        }
        path.write_text(json.dumps(preset, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    def delete(self, template_id: str, name: str) -> bool:
        """Delete a preset. Returns True if deleted, False if not found."""
        path = self._preset_path(template_id, name)
        if path.exists():
            path.unlink()
            return True
        return False

    def get_info(self, template_id: str, name: str) -> Optional[dict]:
        """Get preset metadata (without params)."""
        path = self._preset_path(template_id, name)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {
                "name": data.get("name", name),
                "template_id": data.get("template_id", template_id),
                "description": data.get("description", ""),
                "created_at": data.get("created_at", ""),
                "param_count": len(data.get("params", {})),
            }
        except Exception:
            return None
