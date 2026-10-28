# -*- coding: utf-8 -*-
"""Tests for core.fragment_manager (extractor + combiner + library)."""
import json
from pathlib import Path

import pytest

from core.fragment_manager.extractor import FragmentExtractor, FRAGMENT_SPEC_VERSION
from core.fragment_manager.combiner import FragmentCombiner
from core.fragment_manager.library import FragmentLibrary
from conftest import make_standard_zip, standard_params


class TestFragmentExtractor:
    def test_extract_ok(self, tmp_path, standard_tpl):
        out = tmp_path / "fragments"
        path = FragmentExtractor().extract(str(standard_tpl), (0, 0), str(out))
        frag_dir = Path(path)
        assert frag_dir.exists()
        # full renderable file set extracted
        assert (frag_dir / "motion.json").exists()
        assert (frag_dir / "params.schema.json").exists()
        assert (frag_dir / "adapter.py").exists()
        assert (frag_dir / "render.mjs").exists()
        # fragment.json generated per spec §6
        meta = json.loads((frag_dir / "fragment.json").read_text(encoding="utf-8"))
        assert meta["spec"]["fragment"] == FRAGMENT_SPEC_VERSION
        assert meta["name"] == "测试模板 · 场景 1~1"
        assert meta["sceneRange"] == [0, 0]
        assert "duration" in meta
        assert "runtime" in meta
        assert "parameters" in meta
        assert "compatibility" in meta

    def test_extract_range(self, tmp_path, standard_tpl):
        out = tmp_path / "fragments"
        path = FragmentExtractor().extract(str(standard_tpl), (1, 2), str(out))
        meta = json.loads((Path(path) / "fragment.json").read_text(encoding="utf-8"))
        assert meta["sceneRange"] == [1, 2]

    def test_range_out_of_bounds(self, tmp_path, standard_tpl):
        with pytest.raises(ValueError):
            FragmentExtractor().extract(str(standard_tpl), (0, 30), str(tmp_path))

    def test_range_invalid(self, tmp_path, standard_tpl):
        with pytest.raises(ValueError):
            FragmentExtractor().extract(str(standard_tpl), (-1, 2), str(tmp_path))
        with pytest.raises(ValueError):
            FragmentExtractor().extract(str(standard_tpl), (3, 2), str(tmp_path))

    def test_personal_rejected(self, tmp_path, personal_tpl_v2):
        with pytest.raises(ValueError) as e:
            FragmentExtractor().extract(str(personal_tpl_v2), (0, 0), str(tmp_path))
        assert "仅支持 standard" in str(e.value)

    def test_template_missing(self, tmp_path):
        with pytest.raises(ValueError):
            FragmentExtractor().extract(
                str(tmp_path / "nope.motion"), (0, 0), str(tmp_path))


class TestFragmentCombiner:
    def test_combine_two_fragments(self, tmp_path, standard_tpl):
        ex = FragmentExtractor()
        f1 = Path(ex.extract(str(standard_tpl), (0, 0), str(tmp_path / "lib")))
        f2 = Path(ex.extract(str(standard_tpl), (1, 1), str(tmp_path / "lib")))
        # give second fragment a distinct id for the container
        meta2 = json.loads((f2 / "fragment.json").read_text(encoding="utf-8"))
        meta2["id"] = "frag-2"
        (f2 / "fragment.json").write_text(json.dumps(meta2, ensure_ascii=False),
                                          encoding="utf-8")

        out = tmp_path / "combined.motion"
        combiner = FragmentCombiner()
        result = combiner.combine(
            [{"id": "frag-1", "path": str(f1)},
             {"id": "frag-2", "path": str(f2)}],
            output_path=str(out))
        assert Path(result).exists()
        assert Path(result).stat().st_size > 0
        # container holds scheduling adapter + fragment subdirs
        import zipfile
        with zipfile.ZipFile(result) as z:
            names = z.namelist()
            assert "motion.json" in names
            assert "adapter.py" in names
            assert any(n.startswith("fragments/frag-1/") for n in names)
            assert any(n.startswith("fragments/frag-2/") for n in names)

    def test_combine_missing_fragment(self, tmp_path):
        with pytest.raises(ValueError):
            FragmentCombiner().combine(
                [{"id": "x", "path": str(tmp_path / "nope")}],
                output_path=str(tmp_path / "out.motion"))

    def test_combine_missing_adapter(self, tmp_path):
        fake = tmp_path / "fake"
        fake.mkdir()
        (fake / "motion.json").write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError):
            FragmentCombiner().combine(
                [{"id": "x", "path": str(fake)}],
                output_path=str(tmp_path / "out.motion"))


class TestFragmentLibrary:
    def test_list_empty(self, tmp_path):
        lib = FragmentLibrary(str(tmp_path / "lib"))
        assert lib.list() == []

    def test_extract_and_list(self, tmp_path, standard_tpl):
        FragmentExtractor().extract(str(standard_tpl), (0, 0),
                                    str(tmp_path / "lib"))
        lib = FragmentLibrary(str(tmp_path / "lib"))
        fragments = lib.list()
        assert len(fragments) == 1
        assert fragments[0]["name"] == "测试模板 · 场景 1~1"
        assert fragments[0]["id"] == "test-standard-scene0-0"

    def test_list_filter_category(self, tmp_path, standard_tpl):
        FragmentExtractor().extract(str(standard_tpl), (0, 0),
                                    str(tmp_path / "lib"))
        lib = FragmentLibrary(str(tmp_path / "lib"))
        assert len(lib.list(category="scene")) == 1
        assert lib.list(category="nope") == []

    def test_search(self, tmp_path, standard_tpl):
        FragmentExtractor().extract(str(standard_tpl), (0, 0),
                                    str(tmp_path / "lib"))
        lib = FragmentLibrary(str(tmp_path / "lib"))
        assert len(lib.search("测试")) == 1
        assert len(lib.search("不存在关键词")) == 0

    def test_get(self, tmp_path, standard_tpl):
        FragmentExtractor().extract(str(standard_tpl), (0, 0),
                                    str(tmp_path / "lib"))
        lib = FragmentLibrary(str(tmp_path / "lib"))
        meta = lib.get("test-standard-scene0-0")
        assert meta["name"] == "测试模板 · 场景 1~1"
        assert "path" in meta
        with pytest.raises(KeyError):
            lib.get("no-such-id")
