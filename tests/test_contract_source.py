"""多源契约解析测试（P2 多模式兼容）。

覆盖四级来源：
A contract-json / B ecosystem-content-schema / C guide-declared / D none
重点回归：B 级禁用裸 slot 名兜底，避免兄弟 section 同名槽造成假阳。
"""

import json
from pathlib import Path

import pytest

from core.audit import audit_slots
from core.audit.contract_source import (
    KIND_CONTRACT,
    KIND_CONTENT_SCHEMA,
    KIND_GUIDE,
    KIND_NONE,
    resolve_contract,
    slots_from_content_schema,
)


def _write_json(p: Path, data) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_resolve_prefers_contract_json(tmp_path: Path):
    root = tmp_path / "a"
    root.mkdir()
    _write_json(root / "copy-contract.json",
                {"slots": {"hero": {"title": {"sourceFunction": "drawHero"}}}})
    _write_json(root / "content.schema.json",
                {"fields": [{"id": "A.text001", "unit": "A"}]})
    src = resolve_contract(root)
    assert src.kind == KIND_CONTRACT
    assert src.line_anchor is True
    assert src.total == 1


def test_resolve_content_schema_fields(tmp_path: Path):
    root = tmp_path / "b"
    root.mkdir()
    _write_json(root / "content.schema.json", {"profile": "p", "fields": [
        {"id": "A.text001", "unit": "A", "source": "broll.js:3",
         "uses": ["broll.js:3"], "maxChars": 4, "required": True, "default": ""},
        {"id": "B.text002", "unit": "B", "source": "s01.js:2",
         "uses": ["s01.js:2"], "maxChars": 6, "required": False, "default": ""},
    ]})
    (root / "broll.js").write_text("x\ny\nconst A_text001 = 1;\n", encoding="utf-8")
    (root / "s01.js").write_text("y\nconst B_text002 = 2;\n", encoding="utf-8")
    src = resolve_contract(root)
    assert src.kind == KIND_CONTENT_SCHEMA
    assert src.total == 2
    assert src.line_anchor is False
    sections = {s for s, _, _ in src.slots}
    assert sections == {"A", "B"}
    first = src.slots[0][2]
    assert first["sourceDeclaration"] == "A.text001"
    assert first["maxChars"] == 4
    assert first["strictIdentifier"] is True


def test_content_schema_untrusted_line_numbers(tmp_path: Path):
    """source 行号超出文件实际行数时必须弃用行锚（实测官方包 1310 > 254 行）。"""
    root = tmp_path / "b2"
    root.mkdir()
    _write_json(root / "content.schema.json", {"fields": [
        {"id": "A.text001", "unit": "A", "source": "broll.js:1310", "uses": []},
    ]})
    (root / "broll.js").write_text("A.text001\n", encoding="utf-8")
    src = resolve_contract(root)
    meta = src.slots[0][2]
    assert meta["sourceLine"] is None
    assert meta["declaredLine"] == 1310
    assert meta["lineAnchor"] is False
    assert any("行号" in n and "不可" in n for n in src.notes)


def test_content_schema_no_bare_slot_fallback(tmp_path: Path):
    """S14.text013 在源码不存在、但 S15.text013 存在时，不得因裸名兜底而误判 found。"""
    root = tmp_path / "b3"
    root.mkdir()
    _write_json(root / "content.schema.json", {"fields": [
        {"id": "S14.text013", "unit": "S14", "source": "s12_14.js:10", "uses": []},
        {"id": "S15.text013", "unit": "S15", "source": "s15_17.js:5", "uses": []},
    ]})
    (root / "s12_14.js").write_text("noop\nS15.text013\n", encoding="utf-8")
    report = audit_slots(root)
    by_slot = {(f.section, f.slot_id): f.status for f in report.findings}
    assert by_slot[("S14", "text013")] == "missing"   # 兄弟 section 同名不得算命中
    assert by_slot[("S15", "text013")] == "found"
    assert report.healthy is False


def test_resolve_guide_declared(tmp_path: Path):
    root = tmp_path / "c"
    root.mkdir()
    _write_json(root / "guide.json", {"copy": [
        {"param": "title", "label": "标题", "from": ["conversation"], "rule": "取用户内容"},
        {"param": "mode", "label": "模式", "from": ["conversation"], "rule": "随用户"},
    ]})
    (root / "film.js").write_text("const x = 1;\n", encoding="utf-8")
    src = resolve_contract(root)
    assert src.kind == KIND_GUIDE
    assert src.total == 2
    report = audit_slots(root)
    assert report.contract_source == KIND_GUIDE
    assert report.verified is False      # 只有 param 级声明，没做源码校验
    assert report.healthy is None


def test_resolve_none_when_only_encrypted_payload(tmp_path: Path):
    """模拟 49 个 motion-mjs 包：只有 BUILD.json + 加密资产，无任何槽来源。"""
    root = tmp_path / "d"
    root.mkdir()
    _write_json(root / "BUILD.json", {"entry": "assistant/motion.mjs", "bytes": 852945})
    (root / "release.bin").write_bytes(b"\x00\x01")
    src = resolve_contract(root)
    assert src.kind == KIND_NONE
    assert src.total == 0
    report = audit_slots(root)
    assert report.contract_source == KIND_NONE
    assert report.healthy is None
