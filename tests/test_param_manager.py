# -*- coding: utf-8 -*-
"""Tests for core.param_manager (schema + presets + auto)."""
import json
from pathlib import Path

import pytest

from core.param_manager.schema import ParamSchema
from core.param_manager.presets import PresetManager
from core.param_manager import auto


class TestParamSchema:
    def test_validate_pass(self, schema):
        params = {"mode": "standard", "count": 3, "label": "你好",
                  "tags": ["a"], "flag": True, "style": "b"}
        assert ParamSchema(schema).validate(params) == []

    def test_validate_missing_required(self, schema):
        errors = ParamSchema(schema).validate({"count": 1})
        assert any("缺少必填字段" in e or "mode" in e for e in errors)
        assert any("缺少必填字段" in e or "label" in e for e in errors)

    def test_validate_type_mismatch(self, schema):
        errors = ParamSchema(schema).validate(
            {"mode": "standard", "count": "not-int", "label": "x"})
        assert any("期望 integer" in e for e in errors)

    def test_validate_const(self, schema):
        errors = ParamSchema(schema).validate(
            {"mode": "wrong", "count": 1, "label": "ab"})
        assert any("固定值" in e or "const" in e for e in errors)

    def test_validate_enum(self, schema):
        errors = ParamSchema(schema).validate(
            {"mode": "standard", "count": 1, "label": "ab", "style": "zzz"})
        assert any("枚举" in e for e in errors)

    def test_validate_range(self, schema):
        errors = ParamSchema(schema).validate(
            {"mode": "standard", "count": 99, "label": "ab"})
        assert any("最大值" in e for e in errors)
        errors2 = ParamSchema(schema).validate(
            {"mode": "standard", "count": 0, "label": "ab"})
        assert any("最小值" in e for e in errors2)

    def test_validate_length(self, schema):
        errors = ParamSchema(schema).validate(
            {"mode": "standard", "count": 1, "label": "x"})  # minLength 2
        assert any("minLength" in e or "下限" in e for e in errors)
        errors2 = ParamSchema(schema).validate(
            {"mode": "standard", "count": 1, "label": "太长太长太长太长太长"})
        assert any("maxLength" in e or "上限" in e for e in errors2)

    def test_validate_array_items(self, schema):
        errors = ParamSchema(schema).validate(
            {"mode": "standard", "count": 1, "label": "ab", "tags": []})
        assert any("minItems" in e or "下限" in e for e in errors)
        errors2 = ParamSchema(schema).validate(
            {"mode": "standard", "count": 1, "label": "ab",
             "tags": ["1", "2", "3", "4"]})
        assert any("maxItems" in e or "上限" in e for e in errors2)

    def test_validate_additional_properties(self, schema):
        errors = ParamSchema(schema).validate(
            {"mode": "standard", "count": 1, "label": "ab", "unknown": 1})
        assert any("未知字段" in e for e in errors)

    def test_validate_empty_schema(self):
        assert ParamSchema({}).validate({"a": 1}) != []

    def test_validate_non_dict(self, schema):
        assert ParamSchema(schema).validate("not dict") != []

    def test_get_defaults(self, schema):
        d = ParamSchema(schema).get_defaults()
        assert d["count"] == 3
        assert d["ratio"] == 1.0
        assert d["flag"] is False
        assert "mode" not in d  # no default

    def test_describe(self, schema):
        d = ParamSchema(schema).describe()
        assert d["mode"]["required"] is True
        assert d["mode"]["type"] == "string"
        assert d["extra"]["required"] is False
        assert d["style"]["enum"] == ["a", "b", "c"]

    def test_list_required_optional(self, schema):
        ps = ParamSchema(schema)
        assert set(ps.list_required()) == {"mode", "count", "label"}
        assert "extra" in ps.list_optional()
        assert "mode" not in ps.list_optional()

    def test_from_file(self, tmp_path, schema):
        f = tmp_path / "schema.json"
        f.write_text(json.dumps(schema), encoding="utf-8")
        assert ParamSchema.from_file(str(f)).validate({}) != []

    def test_from_template(self, standard_tpl):
        ps = ParamSchema.from_template(str(standard_tpl))
        assert ps is not None
        assert "scenes" in ps.schema["properties"]

    def test_from_template_bad(self, tmp_path):
        f = tmp_path / "x.motion"
        f.write_bytes(b"garbage")
        assert ParamSchema.from_template(str(f)) is None


class TestPresetManager:
    def test_save_load(self, tmp_path):
        mgr = PresetManager(str(tmp_path / "presets"))
        mgr.save("tpl-1", "默认", {"mode": "standard"}, description="d")
        data = mgr.load("tpl-1", "默认")
        assert data == {"mode": "standard"}

    def test_list_filtered(self, tmp_path):
        mgr = PresetManager(str(tmp_path / "presets"))
        mgr.save("tpl-1", "a", {"x": 1})
        mgr.save("tpl-2", "b", {"y": 2})
        assert mgr.list("tpl-1") == ["a"]
        assert mgr.list("tpl-2") == ["b"]
        assert "tpl-1/a" in mgr.list()
        assert "tpl-2/b" in mgr.list()

    def test_load_missing(self, tmp_path):
        mgr = PresetManager(str(tmp_path / "presets"))
        assert mgr.load("tpl-x", "nope") is None

    def test_delete(self, tmp_path):
        mgr = PresetManager(str(tmp_path / "presets"))
        mgr.save("tpl-1", "a", {"x": 1})
        assert mgr.delete("tpl-1", "a") is True
        assert mgr.load("tpl-1", "a") is None
        assert mgr.delete("tpl-1", "a") is False

    def test_safe_names(self, tmp_path):
        mgr = PresetManager(str(tmp_path / "presets"))
        mgr.save("bad/id", "na/me", {"x": 1})
        assert mgr.load("bad/id", "na/me") == {"x": 1}


class TestAutoParams:
    def test_const_default_enum(self, schema):
        p = auto.build_default_params(schema)
        assert p["mode"] == "standard"  # const
        assert p["count"] == 3  # default
        assert p["style"] == "a"  # enum first
        assert p["flag"] is False

    def test_user_params_priority(self, schema):
        p = auto.build_default_params(schema, user_params={"count": 7})
        assert p["count"] == 7

    def test_fill_optional_false(self, schema):
        p = auto.build_default_params(schema, fill_optional=False)
        assert set(p.keys()) == {"mode", "count", "label"}  # required only

    def test_typed_fallback(self):
        s = {"properties": {"s": {"type": "string"},
                            "n": {"type": "number"},
                            "i": {"type": "integer", "minimum": 5},
                            "b": {"type": "boolean"},
                            "arr": {"type": "array",
                                    "items": {"type": "string"}, "minItems": 2},
                            "obj": {"type": "object",
                                    "properties": {"k": {"type": "string"}}}},
             "required": ["s", "n", "i", "b", "arr", "obj"]}
        p = auto.build_default_params(s)
        assert p["s"] == "示例内容"
        assert p["n"] == 1
        assert p["i"] == 5
        assert p["b"] is False
        assert p["arr"] == ["示例内容", "示例内容"]
        # 嵌套 object：子字段非 required 时按可选兜底生成空值
        assert p["obj"] == {"k": ""}

    def test_build_default_scenes(self):
        s = {"items": {"type": "object", "required": ["id", "title"],
                       "properties": {"id": {"type": "string"},
                                      "title": {"type": "string"},
                                      "dur": {"type": "number", "default": 4.0}}}}
        scenes = auto.build_default_scenes(s, count=2)
        assert len(scenes) == 2
        assert scenes[0]["id"] == "S01"
        assert scenes[1]["id"] == "S02"
        assert scenes[0]["dur"] == 4.0
