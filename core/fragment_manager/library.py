"""Fragment library - store, search, and manage reusable animation fragments.

库 = 片段目录集合：每个片段是一个包含 fragment.json 的目录（由
FragmentExtractor 产出），或可直接提取的 .motion 模板。本实现为
F2 测试补全的最小可用版：list/search/get 从目录扫描 fragment.json 元数据。
"""

from __future__ import annotations

import json
from pathlib import Path


class FragmentLibrary:
    """Library of reusable animation fragments."""

    def __init__(self, library_dir: str):
        self.library_dir = library_dir

    def _fragment_dirs(self) -> list[Path]:
        """枚举库内所有含 fragment.json 的片段目录。"""
        root = Path(self.library_dir)
        if not root.exists():
            return []
        return sorted(
            d for d in root.iterdir()
            if d.is_dir() and (d / "fragment.json").exists()
        )

    @staticmethod
    def _read_meta(frag_dir: Path) -> dict:
        return json.loads((frag_dir / "fragment.json").read_text(encoding="utf-8"))

    def list(self, category: str = None) -> list[dict]:
        """List fragments, optionally filtered by category."""
        result = []
        for d in self._fragment_dirs():
            meta = self._read_meta(d)
            if category and meta.get("category") != category:
                continue
            result.append({"id": meta.get("id"), "name": meta.get("name"),
                           "category": meta.get("category"),
                           "tags": meta.get("tags", []),
                           "duration": meta.get("duration"),
                           "path": str(d)})
        return result

    def search(self, query: str) -> list[dict]:
        """Search fragments by keywords (name/id/tags/category)."""
        q = (query or "").lower()
        if not q:
            return self.list()
        result = []
        for d in self._fragment_dirs():
            meta = self._read_meta(d)
            hay = " ".join([
                str(meta.get("id", "")), str(meta.get("name", "")),
                str(meta.get("category", "")),
                " ".join(meta.get("tags", [])),
            ]).lower()
            if q in hay:
                result.append({"id": meta.get("id"), "name": meta.get("name"),
                               "category": meta.get("category"),
                               "tags": meta.get("tags", []),
                               "path": str(d)})
        return result

    def get(self, fragment_id: str) -> dict:
        """Get fragment metadata by ID."""
        for d in self._fragment_dirs():
            meta = self._read_meta(d)
            if meta.get("id") == fragment_id:
                meta["path"] = str(d)
                return meta
        raise KeyError(f"片段不存在: {fragment_id}")
