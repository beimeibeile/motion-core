# -*- coding: utf-8 -*-
"""Tests for standard-template contract adapter (引擎适配缺口修复).

验证：build_standard_params 从 catalog 原生约束生成契约合法参数
（scenes 锁时长、B 组 basic、media 绑定、duration=Σ原生时长、
Basic 表达式文本容量上限）。
"""
import json

import pytest

from core.param_manager import (
    build_standard_params,
    group_duration,
    group_media_roles,
    BASIC_REQUIREMENTS,
    BASIC_LIMITS,
    TEXT_ONLY_IDS,
    basic_for_group,
    validate_basic_text,
)


@pytest.fixture
def catalog(tmp_path):
    """迷你 catalog（B1=8s/B2=10s，锁时长无 hold；A 组带 wall 素材）。"""
    c = {
        "profile": "adu-auto-brief/1",
        "groups": [
            {"id": "B1", "start": 250.0, "end": 258.0,
             "maxReadingHold": 12, "fields": [], "media": []},
            {"id": "B2", "start": 260.0, "end": 270.0,
             "maxReadingHold": 12, "fields": [], "media": []},
            {"id": "A", "start": 4.65, "end": 10.7833,
             "maxReadingHold": None, "fields": ["A.text001"], "media": ["wall"]},
        ],
        "mediaPorts": {},
    }
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps(c), encoding="utf-8")
    return str(p)


class TestStandardAdapter:
    """standard 契约适配器（引擎缺口修复 + 实例任务固化）。"""

    def test_default_build(self, catalog):
        p = build_standard_params(catalog, title="新能源速览", topic="新能源")
        assert p["mode"] == "user-media"
        assert p["layout"] == "landscape"
        assert p["duration"] == 18.0          # B1 8s + B2 10s
        assert p["scenes"] == [
            {"id": "B1", "title": "新能源"}, {"id": "B2", "title": "新能源"}]
        # scenes 不含 duration（锁时长契约）
        assert all("duration" not in s for s in p["scenes"])
        # B 组 basic 齐全
        assert p["basic"]["keyword"] == "新能源"
        assert len(p["basic"]["points"]) == 2
        # media 三 none、content 空、captions 空
        assert all(v["mode"] == "none"
                   for v in p["media"].values())
        assert p["content"] == {}
        assert p["captions"] == []

    def test_required_fields_present(self, catalog):
        p = build_standard_params(catalog, topic="主题")
        for key in ("mode", "language", "layout", "duration", "title",
                    "presenterLabel", "scenes", "content", "media",
                    "soundEffects", "captions"):
            assert key in p

    def test_single_group_duration(self, catalog):
        p = build_standard_params(catalog, selected=["B1"], topic="t")
        assert p["duration"] == 8.0
        assert len(p["scenes"]) == 1

    def test_media_group_requires_binding(self, catalog):
        """素材组未提供素材 → 拒绝。"""
        with pytest.raises(ValueError, match="需绑定素材 role 'wall'"):
            build_standard_params(catalog, selected=["A"], topic="t")

    def test_media_group_bound_image(self, catalog):
        """素材组提供 image 素材 + content 字段 → 通过。"""
        p = build_standard_params(
            catalog, selected=["A"], topic="t",
            content={"A.text001": "钱学森"}, media={"wall": "C:/mat/a.jpg"})
        assert p["media"]["wall"] == {"mode": "image", "file": "C:/mat/a.jpg"}
        assert p["content"]["A.text001"] == "钱学森"
        assert p["duration"] == round(10.7833 - 4.65, 3)

    def test_media_group_video_mode(self, catalog):
        p = build_standard_params(
            catalog, selected=["A"], topic="t",
            media={"wall": {"mode": "video", "file": "C:/mat/a.mp4"}})
        assert p["media"]["wall"]["mode"] == "video"

    def test_media_group_bad_mode_rejected(self, catalog):
        with pytest.raises(ValueError, match="仅支持 image/video"):
            build_standard_params(
                catalog, selected=["A"], topic="t",
                media={"wall": {"mode": "audio", "file": "C:/a.mp3"}})

    def test_duration_mismatch_rejected(self, catalog):
        with pytest.raises(ValueError, match="原生时长"):
            build_standard_params(catalog, duration=19.0, topic="t")

    def test_explicit_duration_matches(self, catalog):
        p = build_standard_params(catalog, duration=18.0, topic="t")
        assert p["duration"] == 18.0

    def test_language_validation(self, catalog):
        with pytest.raises(ValueError, match="language"):
            build_standard_params(catalog, language="jp", topic="t")

    def test_basic_limit_enforced(self):
        """zh 容量：heading 10、keyword 8、explanation 42。"""
        assert BASIC_LIMITS["heading"] == 10
        assert BASIC_LIMITS["keyword"] == 8
        with pytest.raises(ValueError, match="exceeds capacity"):
            validate_basic_text("heading", "这是一个超过十个字符的标题文本")
        # 不超限则不抛
        validate_basic_text("heading", "八个字符的标题文本")

    def test_basic_for_group_clips_long_topic(self):
        d = basic_for_group("B1", "这是一个非常非常长的主题名称", "zh")
        assert len(d["keyword"]) <= BASIC_LIMITS["keyword"]
        assert len(d["explanation"]) <= BASIC_LIMITS["explanation"]

    def test_content_only_group_passthrough(self, catalog):
        p = build_standard_params(catalog, selected=["A"], topic="t",
                                  content={"A.text001": "x", "B1.textX": "y"},
                                  media={"wall": "C:/a.jpg"})
        # 只透传选中组前缀字段
        assert p["content"] == {"A.text001": "x"}

    def test_group_media_roles_helper(self, catalog):
        assert group_media_roles(json.load(open(catalog, encoding="utf-8")),
                                 "A") == ["wall"]
        assert group_media_roles(json.load(open(catalog, encoding="utf-8")),
                                 "B1") == []
        assert BASIC_REQUIREMENTS["B2"] == ["heading", "points"]
        assert "B1" in TEXT_ONLY_IDS and "A" not in TEXT_ONLY_IDS
