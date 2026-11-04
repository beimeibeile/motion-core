# -*- coding: utf-8 -*-
"""P2 参数槽失效审计测试（OpenFilm 借鉴：copy-contract 维护 / role 契约）。

覆盖：源码引用校验（found/moved/missing）、schema 双向 diff
（contract_only/schema_only）、报告输出、invoke 注册。
"""
import json
from pathlib import Path

import pytest

from core.audit import (
    audit_slots,
    find_copy_contract,
    find_schema,
    find_js_sources,
    verify_slot_in_source,
    extract_schema_copy_slots,
    compare_schema,
    FOUND,
    MOVED,
    MISSING,
    CONTRACT_ONLY,
    SCHEMA_ONLY,
)
from core.invoke import invoke


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def audit_fixture(tmp_path: Path) -> Path:
    """构造确定性审计样板：
    - copy-contract.json：hero.title（found）/ hero.subtitle（moved）/
      hero.ghost（missing）/ recipe.lines_01（found）
    - film.js：行号与标识设计好三态
    - params.schema.json：hero.title / hero.sub 保留（→ contract_only 缺
      subtitle/ghost；schema_only 多 sub）；recipe.lines_01 保留
    """
    root = tmp_path / "tpl"
    root.mkdir()
    contract = {
        "profile": "motiongit.main-product-copy/1",
        "slots": {
            "hero": {
                "title": {"section": "hero", "id": "title",
                          "sourceFunction": "drawHero", "sourceLine": 10,
                          "sourceOccurrences": 1, "maxCharacters": 64,
                          "kind": "plain"},
                "subtitle": {"section": "hero", "id": "subtitle",
                             "sourceFunction": "drawHero", "sourceLine": 20,
                             "sourceOccurrences": 1, "maxCharacters": 64,
                             "kind": "plain"},
                "ghost": {"section": "hero", "id": "ghost",
                          "sourceDeclaration": "GHOST_STRINGS",
                          "sourceLine": 30, "sourceOccurrences": 1,
                          "maxCharacters": 64, "kind": "plain"},
            },
            "recipe": {
                "lines_01": {"section": "recipe", "id": "lines_01",
                             "sourceFunction": "drawRecipe", "sourceLine": 30,
                             "sourceOccurrences": 1, "maxCharacters": 64,
                             "kind": "plain"},
            },
        },
    }
    _write_json(root / "copy-contract.json", contract)

    film_lines = [
        'const HERO_TITLES = ["科技风"];',   # L1：标识存在（但槽用 sourceFunction）
        'const GHOST = 1;',                  # L2：不含 GHOST_STRINGS（避免前缀误命中）
        'const OLD = ["x"];',                # L3：GHOST_STRINGS 完全消失
        "",
        "",
        "",
        "",
        "",
        "",
        "function drawHero() { return 1; }",  # L10：drawHero 定义
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "const foo = 1;",                     # L20：无 drawHero → moved
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "function drawRecipe() { return 2; }",  # L30：drawRecipe 定义
    ]
    (root / "native").mkdir()
    (root / "native" / "film.js").write_text(
        "\n".join(film_lines), encoding="utf-8")

    schema = {
        "type": "object",
        "properties": {
            "copy": {
                "type": "object",
                "properties": {
                    "hero": {"type": "object", "properties": {
                        "title": {"type": "string"},
                        "sub": {"type": "string"},   # schema_only
                    }},
                    "recipe": {"type": "object", "properties": {
                        "lines_01": {"type": "string"},
                    }},
                },
            },
        },
    }
    _write_json(root / "params.schema.json", schema)

    # 干扰项：node_modules 下同名契约/源码不应被扫到
    nm = root / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    _write_json(nm / "copy-contract.json", {"slots": {"x": {}}})
    (nm / "film.js").write_text("const x = 1;", encoding="utf-8")
    return root


def test_find_files_exclude_node_modules(audit_fixture: Path):
    contract = find_copy_contract(audit_fixture)
    assert contract.name == "copy-contract.json"
    assert "node_modules" not in contract.parts
    assert contract.parent == audit_fixture
    schema = find_schema(audit_fixture)
    assert schema.name == "params.schema.json"
    assert "node_modules" not in schema.parts
    srcs = find_js_sources(audit_fixture)
    assert len(srcs) == 1
    assert "node_modules" not in srcs[0].parts


def test_verify_slot_statuses(audit_fixture: Path):
    from core.audit import load_contract_slots
    from core.audit.slot_audit import _source_text
    slots = load_contract_slots(audit_fixture / "copy-contract.json")
    texts = _source_text(find_js_sources(audit_fixture))
    by_id = {(s, i): m for s, i, m in slots}

    f = verify_slot_in_source("hero", "title", by_id[("hero", "title")], texts)
    assert f.status == FOUND
    assert any("第 10 行匹配" in e for e in f.evidence)

    f = verify_slot_in_source("hero", "subtitle", by_id[("hero", "subtitle")], texts)
    assert f.status == MOVED
    assert any("行号已偏移" in e for e in f.evidence)

    f = verify_slot_in_source("hero", "ghost", by_id[("hero", "ghost")], texts)
    assert f.status == MISSING
    assert any("均未命中" in e for e in f.evidence)

    f = verify_slot_in_source("recipe", "lines_01", by_id[("recipe", "lines_01")], texts)
    assert f.status == FOUND


def test_compare_schema_bidirectional(audit_fixture: Path):
    from core.audit import load_contract_slots
    slots = load_contract_slots(audit_fixture / "copy-contract.json")
    findings, warnings = compare_schema(slots, audit_fixture / "params.schema.json")
    statuses = {f.status for f in findings}
    assert CONTRACT_ONLY in statuses and SCHEMA_ONLY in statuses
    contract_only = {f.slot_id for f in findings if f.status == CONTRACT_ONLY}
    schema_only = {f.slot_id for f in findings if f.status == SCHEMA_ONLY}
    assert contract_only == {"subtitle", "ghost"}
    assert schema_only == {"sub"}
    assert warnings == []


def test_extract_schema_copy_slots_v2_and_flat(tmp_path: Path):
    v2 = tmp_path / "v2.json"
    _write_json(v2, {"properties": {"copy": {"properties": {
        "hero": {"properties": {"title": {}, "sub": {}}},
        "pack": {"properties": {"code_01": {}}},
    }}}})
    slots = extract_schema_copy_slots(v2)
    assert set(slots) == {("hero", "title"), ("hero", "sub"), ("pack", "code_01")}

    flat = tmp_path / "flat.json"
    _write_json(flat, {"properties": {"copy": {"properties": {
        "title": {"type": "string"}, "sub": {"type": "string"},
    }}}})
    slots = extract_schema_copy_slots(flat)
    assert set(slots) == {("copy", "title"), ("copy", "sub")}


def test_audit_report_full(audit_fixture: Path):
    report = audit_slots(audit_fixture)
    assert report.total_slots == 4
    summary = report.summary
    assert summary[FOUND] == 2          # hero.title + recipe.lines_01
    assert summary[MOVED] == 1          # hero.subtitle
    assert summary[MISSING] == 1        # hero.ghost
    assert summary[CONTRACT_ONLY] == 2  # subtitle/ghost 契约有、schema 无
    assert summary[SCHEMA_ONLY] == 1    # hero.sub 新增未登记
    assert report.healthy is False
    # 只读审计：不修改任何文件
    assert (audit_fixture / "copy-contract.json").exists()
    assert "copy-contract.json" in report.contract_path


def test_audit_healthy_when_no_issue(tmp_path: Path):
    root = tmp_path / "ok"
    root.mkdir()
    _write_json(root / "copy-contract.json", {"slots": {
        "hero": {"title": {"sourceFunction": "drawHero", "sourceLine": 1,
                            "sourceOccurrences": 1}},
    }})
    _write_json(root / "params.schema.json", {"properties": {"copy": {
        "properties": {"hero": {"properties": {"title": {}}}}}}})
    (root / "film.js").write_text("function drawHero() {}\n", encoding="utf-8")
    report = audit_slots(root)
    assert report.healthy is True
    assert report.summary[FOUND] == 1


def test_audit_no_contract_reports_none(tmp_path: Path):
    """无任何契约来源：不 raise，明确报 none 且健康状态未知。"""
    root = tmp_path / "noc"
    root.mkdir()
    (root / "film.js").write_text("const x = 1;", encoding="utf-8")
    report = audit_slots(root)
    assert report.contract_source == "none"
    assert report.verified is False
    assert report.healthy is None
    assert report.total_slots == 0


def test_audit_contract_without_source_is_unknown(tmp_path: Path):
    """有契约但无渲染源码：登记槽、不判健康（没校验过不等于健康）。"""
    root = tmp_path / "nosrc"
    root.mkdir()
    _write_json(root / "copy-contract.json", {"slots": {"hero": {"title": {}}}})
    report = audit_slots(root)
    assert report.contract_source == "contract-json"
    assert report.total_slots == 1
    assert report.verified is False
    assert report.healthy is None
    assert any("渲染源码" in w for w in report.warnings)


def test_report_to_dict_and_markdown(audit_fixture: Path):
    report = audit_slots(audit_fixture)
    d = report.to_dict()
    assert d["healthy"] is False
    assert d["total_slots"] == 4
    assert "findings" in d and len(d["findings"]) >= 6
    md = report.to_markdown()
    assert "参数槽失效审计报告" in md
    assert "只报告不修改" in md


def test_invoke_motion_slot_audit(audit_fixture: Path):
    r = invoke("motion_slot_audit", template_dir=str(audit_fixture))
    assert r["success"] is True
    result = r["result"]
    assert result["summary"][MISSING] == 1
    assert result["summary"][CONTRACT_ONLY] == 2


def test_invoke_missing_dir_fails():
    r = invoke("motion_slot_audit", template_dir="Z:/no/such/dir")
    assert r["success"] is False
    assert r["code"] == "EXECUTION_ERROR"
