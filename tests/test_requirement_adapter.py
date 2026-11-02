# -*- coding: utf-8 -*-
"""Tests for 模板需求智能处理层（任务 #52 / I8 前置）。

验证：整篇台词 → 语义阶段拆解 → 场景映射（B 组纯文本 / 素材组判定）
→ 素材槽位判定（needs-binding 阻塞）→ 契约参数生成（台词字段覆盖 topic 示例）
→ 缺项阻塞报告（未通过不得进渲染）。
"""
import json

import pytest

from core.producer.requirement_adapter import (
    RequirementAdapter,
    analyze_script,
    classify_assets,
    split_sentences,
)


@pytest.fixture
def catalog(tmp_path):
    """迷你 catalog：B1/B2 纯文本 + A 组（wall 素材）。"""
    c = {
        "profile": "kinetic-text-slots/1",
        "groups": [
            {"id": "B1", "start": 0.0, "end": 8.0, "media": []},
            {"id": "B2", "start": 8.0, "end": 18.0, "media": []},
            {"id": "A", "start": 20.0, "end": 26.133, "media": ["wall"]},
        ],
    }
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    return str(p)


# ---------- 台词拆解 ----------

def test_split_sentences():
    assert split_sentences("第一句。第二句！第三句？") == ["第一句", "第二句", "第三句"]
    assert split_sentences("单句") == ["单句"]
    assert split_sentences("") == []


def test_analyze_script_four_stages():
    script = "产品参数很关键。续航提升百分之三十。价格保持不变。现在即可下单。"
    stages = analyze_script(script)
    assert len(stages) == 4
    # 标题来自首句、保留实义
    assert stages[0].title == "产品参数很关键"
    # 关键 cue（数字/否定）保留
    assert "百分之三十" in stages[1].cues or "30" in str(stages[1].cues)
    assert "不" in stages[2].cues
    # 覆盖所有保留台词且不错序
    assert "".join(s.text for s in stages) == script.replace("。", "").replace("！", "").replace("？", "")


def test_analyze_script_merges_overflow():
    # 5 句 → 强制 4 段（B 组上限）
    script = "一。二。三。四。五。"
    stages = analyze_script(script, max_stages=4)
    assert len(stages) == 4
    assert "".join(s.text for s in stages) == "一二三四五"


# ---------- 素材分类 ----------

def test_classify_assets_by_role():
    assets = {"presenter": "p.mp4", "wall": "bg.jpg"}
    roles = classify_assets(assets)
    assert roles["presenter"] == "p.mp4"
    assert roles["footage"] == "bg.jpg"   # wall 按扩展名归为 footage


# ---------- 场景映射 ----------

def test_plan_text_route_no_media(catalog):
    ra = RequirementAdapter(catalog)
    plan = ra.plan("第一句。第二句。")
    assert plan.route == "text"
    assert plan.selected == ["B1", "B2"]
    assert not plan.blocked


def test_plan_text_route_blocks_empty_script(catalog):
    ra = RequirementAdapter(catalog)
    plan = ra.plan("   ")
    assert plan.blocked
    assert any("台词为空" in b for b in plan.blockers)


def test_plan_text_route_blocks_overflow(catalog):
    ra = RequirementAdapter(catalog)
    # 显式要求 5 段但走纯文本路线（B 组上限 4）→ 阻塞
    plan = ra.plan("一。二。三。四。五。", max_stages=5)
    assert plan.blocked
    assert any("超过 B 组上限" in b for b in plan.blockers)


def test_plan_media_route_missing_role_blocks(catalog):
    ra = RequirementAdapter(catalog)
    # 素材键名直配：thumbnail ≠ A 组需要的 wall → 无候选 → needs-binding 阻塞
    plan = ra.plan("第一句。", assets={"thumbnail": "cover.jpg"})
    assert plan.blocked
    assert any("needs-binding" in b or "无 media 组可覆盖" in b for b in plan.blockers)


def test_plan_media_route_provided_ok(catalog):
    ra = RequirementAdapter(catalog)
    plan = ra.plan("第一句。", assets={"wall": "bg.jpg"})
    assert plan.route == "media"
    assert plan.selected == ["A"]
    assert not plan.blocked
    assert "wall" in plan.media_required


# ---------- 契约参数生成 ----------

def test_build_contract_text_route_uses_stage_text(catalog):
    ra = RequirementAdapter(catalog)
    plan = ra.plan("产品参数很关键。续航提升百分之三十。")
    result = ra.build(plan, title="参数测试", topic="参数测试")
    assert not result.blocked
    p = result.params
    assert p["duration"] == pytest.approx(18.0)      # B1(8)+B2(10)
    # B1.keyword 覆盖为台词拆解标题（非 topic 示例）
    assert p["basic"]["keyword"] == "产品参数很关键"
    # B1.explanation 来自第一语义阶段全文
    assert p["basic"]["explanation"] == "产品参数很关键"
    # B2.heading 来自第二段标题
    assert p["basic"]["heading"] == "续航提升百分之三十"
    # contentEvidence / ownershipRefs
    assert result.content_evidence == ["产品参数很关键", "续航提升百分之三十"]
    assert result.ownership_refs == {"B1": "产品参数很关键", "B2": "续航提升百分之三十"}


def test_build_blocked_when_plan_blocked(catalog):
    ra = RequirementAdapter(catalog)
    plan = ra.plan("   ")
    result = ra.build(plan)
    assert result.blocked
    assert result.params is None


def test_build_capacity_warning(catalog):
    ra = RequirementAdapter(catalog)
    # B1.keyword 容量 8 字，标题超限 → 截断 + warning
    script = "这是一条超过八个字的超长产品名称介绍。第二句。"
    plan = ra.plan(script)
    result = ra.build(plan, title="t", topic="t")
    assert not result.blocked
    assert len(result.params["basic"]["keyword"]) <= 8
    assert any("超" in w and "截断" in w for w in plan.warnings)


def test_build_media_route_contract(catalog):
    ra = RequirementAdapter(catalog)
    plan = ra.plan("第一句。", assets={"wall": "bg.jpg"})
    result = ra.build(plan, media={"wall": "bg.jpg"}, title="t", topic="t")
    assert not result.blocked
    assert result.params["scenes"][0]["id"] == "A"
    assert result.params["media"]["wall"]["mode"] == "image"
    assert result.params["duration"] == pytest.approx(6.133)


def test_build_media_route_requires_content(catalog, tmp_path):
    """素材组 required content 缺失 → needs-binding 阻塞（S 组文本不为空）。"""
    import json as _json
    schema = {"profile": "kinetic-text-slots/1", "fields": [
        {"id": "A.text001", "required": True, "maxChars": 6, "format": "text",
         "unit": "A", "context": "", "source": "", "uses": [], "label": "",
         "default": ""},
    ]}
    (tmp_path / "content.schema.json").write_text(
        _json.dumps(schema, ensure_ascii=False), encoding="utf-8")
    ra = RequirementAdapter(catalog)
    plan = ra.plan("第一句。", assets={"wall": "bg.jpg"})
    assert plan.blocked
    assert any("缺内容 needs-binding" in b for b in plan.blockers)
    # 补 content 后通过
    plan2 = ra.plan("第一句。", assets={"wall": "bg.jpg"},
                    content={"A.text001": "核心"})
    assert not plan2.blocked
    result = ra.build(plan2, media={"wall": "bg.jpg"},
                      content={"A.text001": "核心"}, title="t", topic="t")
    assert not result.blocked
    assert result.params["content"] == {"A.text001": "核心"}
