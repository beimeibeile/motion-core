# -*- coding: utf-8 -*-
"""审计侧 literal 白名单测试（两个失效槽走白名单、模板包内不改）。

覆盖：命中即 waived 且不影响健康判定、版本不符不豁免、槽已修复后提示清理、
模板标识不匹配不生效、白名单文件损坏不阻塞审计。
"""
import json
from pathlib import Path

import pytest

from core.audit import audit_slots, WAIVED, MISSING, FOUND


def _w(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def tpl(tmp_path: Path) -> Path:
    """hero.ghost 标识在源码中完全消失（确定性 missing），其余两槽 found。"""
    root = tmp_path / "tpl"
    root.mkdir()
    _w(root / "motion.json", {"template": {"id": "tpl-test", "version": "1.0.0"}})
    _w(root / "copy-contract.json", {"slots": {
        "hero": {
            "title": {"section": "hero", "id": "title",
                      "sourceFunction": "drawHero", "sourceLine": 1, "kind": "plain"},
            "ghost": {"section": "hero", "id": "ghost",
                      "sourceDeclaration": "GHOST_STRINGS", "sourceLine": 2, "kind": "plain"},
        },
        "recipe": {"lines_01": {"section": "recipe", "id": "lines_01",
                                "sourceFunction": "drawRecipe", "sourceLine": 3, "kind": "plain"}},
    }})
    (root / "native").mkdir()
    (root / "native" / "film.js").write_text(
        "function drawHero(){}\nconst X = 1;\nfunction drawRecipe(){}\n", encoding="utf-8")
    _w(root / "params.schema.json", {"type": "object", "properties": {"copy": {
        "type": "object", "properties": {
            "hero": {"type": "object", "properties": {
                "title": {"type": "string"}, "ghost": {"type": "string"}}},
            "recipe": {"type": "object", "properties": {"lines_01": {"type": "string"}}},
        }}}})
    return root


def _wl(tmp_path: Path, entries, name="wl.json") -> Path:
    p = tmp_path / name
    _w(p, {"profile": "omotion.slot-literal-whitelist/1", "entries": entries})
    return p


def _find(report, slot):
    return next(f for f in report.findings if f.slot_id == slot)


def test_whitelist_turns_missing_into_waived(tpl: Path, tmp_path: Path):
    wl = _wl(tmp_path, [{"template_id": "tpl-test", "section": "hero", "slots": ["ghost"],
                         "reason": "契约锚错，模板包不改", "decided_at": "2026-11-05"}])
    report = audit_slots(tpl, whitelist_path=str(wl))
    assert _find(report, "ghost").status == WAIVED
    assert report.summary.get(MISSING, 0) == 0
    assert report.healthy is True
    assert any("白名单" in e for e in _find(report, "ghost").evidence)
    assert any("豁免" in w for w in report.warnings)


def test_waived_slot_is_not_deleted_and_stays_counted(tpl: Path, tmp_path: Path):
    wl = _wl(tmp_path, [{"template_id": "tpl-test", "section": "hero", "slots": ["ghost"],
                         "reason": "已确认失效"}])
    report = audit_slots(tpl, whitelist_path=str(wl))
    assert report.total_slots == 3
    assert len(report.findings) == 3
    assert report.summary.get(WAIVED, 0) == 1
    md = report.to_markdown()
    assert "已豁免" in md and "waived" in md


def test_version_mismatch_does_not_waive(tpl: Path, tmp_path: Path):
    wl = _wl(tmp_path, [{"template_id": "tpl-test", "version": "0.9.0",
                         "section": "hero", "slots": ["ghost"], "reason": "旧版本"}])
    report = audit_slots(tpl, whitelist_path=str(wl))
    assert _find(report, "ghost").status == MISSING
    assert report.healthy is False
    assert any("版本" in w and "未应用" in w for w in report.warnings)


def test_other_template_id_is_ignored(tpl: Path, tmp_path: Path):
    wl = _wl(tmp_path, [{"template_id": "another-tpl", "section": "hero",
                         "slots": ["ghost"], "reason": "别家的槽"}])
    report = audit_slots(tpl, whitelist_path=str(wl))
    assert _find(report, "ghost").status == MISSING
    assert not any("豁免" in w for w in report.warnings)


def test_stale_entry_is_reported_after_slot_recovers(tpl: Path, tmp_path: Path):
    wl = _wl(tmp_path, [{"template_id": "tpl-test", "section": "hero", "slots": ["title"],
                         "reason": "早已修好"}])
    report = audit_slots(tpl, whitelist_path=str(wl))
    assert _find(report, "title").status == FOUND
    assert any("建议删除该条" in w for w in report.warnings)


def test_broken_whitelist_does_not_block_audit(tmp_path: Path):
    root = tmp_path / "tpl"
    root.mkdir()
    bad = tmp_path / "wl.json"
    bad.write_text("{ not json", encoding="utf-8")
    report = audit_slots(root, whitelist_path=str(bad))
    assert report.contract_source == "none"
    assert any("白名单无法解析" in w for w in report.warnings)


def test_whitelist_does_not_apply_when_deciding_on_other_state(tpl: Path, tmp_path: Path):
    """白名单只改被判为失效的槽，不给 found 槽叠状态。"""
    wl = _wl(tmp_path, [{"template_id": "tpl-test", "section": "hero", "slots": ["title"],
                         "reason": "误加条目"},
                        {"template_id": "tpl-test", "section": "hero", "slots": ["ghost"],
                         "reason": "真失效"}])
    report = audit_slots(tpl, whitelist_path=str(wl))
    assert _find(report, "title").status == FOUND
    assert _find(report, "ghost").status == WAIVED
