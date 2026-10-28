# -*- coding: utf-8 -*-
"""Tests for standard-template contract adapter (引擎适配缺口修复).

验证：build_standard_params 从 catalog 原生约束生成契约合法参数
（scenes 锁时长、B 组 basic、media 三 none、duration=Σ原生时长）。
"""
import json

import pytest

from core.param_manager import (
    build_standard_params,
    group_duration,
    BASIC_REQUIREMENTS,
    TEXT_ONLY_IDS,
)


@pytest.fixture
def catalog(tmp_path):
    """迷你 catalog（B1=8s/B2=10s，锁时长无 hold）。"""
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
    """I 组遗留：standard 契约适配器。"""

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

    def test_media_group_rejected(self, catalog):
        with pytest.raises(ValueError, match="需绑定真实素材"):
            build_standard_params(catalog, selected=["A"], topic="t")

    def test_duration_mismatch_rejected(self, catalog):
        with pytest.raises(ValueError, match="原生时长"):
            build_standard_params(catalog, duration=19.0, topic="t")

    def test_explicit_duration_matches(self, catalog):
        p = build_standard_params(catalog, duration=18.0, topic="t")
        assert p["duration"] == 18.0

    def test_language_validation(self, catalog):
        with pytest.raises(ValueError, match="language"):
            build_standard_params(catalog, language="jp", topic="t")

    def test_group_duration_helper(self, catalog):
        assert group_duration(json.load(open(catalog, encoding="utf-8")),
                              "B1") == 8.0
        assert BASIC_REQUIREMENTS["B2"] == ["heading", "points"]
        assert "B1" in TEXT_ONLY_IDS and "A" not in TEXT_ONLY_IDS
