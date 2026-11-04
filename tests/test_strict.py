"""P3 严格解析 + 行号报错：strict_json_load 与 lint_template 测试。"""

import json
import os
import tempfile

import pytest

from core.template_parser.strict import (lint_template, strict_json_load,
                                         REQUIRED_FILES, REQUIRED_MOTION_FIELDS)


def _write(dirpath: str, name: str, content: str) -> str:
    p = os.path.join(dirpath, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return p


def test_strict_json_load_ok():
    with tempfile.TemporaryDirectory() as d:
        p = _write(d, "ok.json", '{"a": 1}')
        assert strict_json_load(p) == {"a": 1}


def test_strict_json_load_bom_tolerated():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "bom.json")
        with open(p, "wb") as f:
            f.write(b"\xef\xbb\xbf" + b'{"a": 1}')
        assert strict_json_load(p) == {"a": 1}


def test_strict_json_load_line_col_error():
    with tempfile.TemporaryDirectory() as d:
        p = _write(d, "bad.json", '{\n  "a": 1,\n  "b": 2,  \n}')
        with pytest.raises(ValueError) as ei:
            strict_json_load(p)
        msg = str(ei.value)
        # 尾部多逗号，错误定位在第 3 行
        assert f"{p}:3:" in msg
        assert "JSON 解析失败" in msg


def test_strict_json_load_col_error():
    with tempfile.TemporaryDirectory() as d:
        p = _write(d, "bad2.json", '{"a": 1,,}')
        with pytest.raises(ValueError) as ei:
            strict_json_load(p)
        msg = str(ei.value)
        assert f"{p}:1:" in msg


def test_strict_json_load_missing_file():
    with pytest.raises(ValueError) as ei:
        strict_json_load("C:/no/such/file.json")
    assert "文件不存在" in str(ei.value)


def test_lint_healthy_template():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "motion.json", json.dumps(
            {"id": "t1", "name": "T", "version": "1.0", "runtime": "html-canvas/1"}))
        _write(d, "params.schema.json", json.dumps(
            {"properties": {"copy": {"properties": {}}}}))
        _write(d, "guide.json", json.dumps({"sections": []}))
        os.makedirs(os.path.join(d, "native"))
        r = lint_template(d)
        assert r.healthy
        assert r.summary["errors"] == 0


def test_lint_missing_required_files():
    with tempfile.TemporaryDirectory() as d:
        r = lint_template(d)
        assert not r.healthy
        msgs = [f.message for f in r.findings if f.severity == "error"]
        assert any("motion.json" in m for m in msgs)
        assert any("params.schema.json" in m for m in msgs)
        assert any("guide.json" in m for m in msgs)


def test_lint_bad_json_reports_linecol():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "motion.json", '{"id": "t1", }')
        _write(d, "params.schema.json", "{}")
        _write(d, "guide.json", "{}")
        r = lint_template(d)
        assert not r.healthy
        err = next(f for f in r.findings if f.severity == "error")
        assert "motion.json:1:" in err.message


def test_lint_missing_motion_fields():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "motion.json", json.dumps({"id": "t1"}))
        _write(d, "params.schema.json", "{}")
        _write(d, "guide.json", "{}")
        r = lint_template(d)
        assert not r.healthy
        msgs = [f.message for f in r.findings if f.severity == "error"]
        assert any("version" in m for m in msgs)


def test_lint_optional_contract_bad_json():
    with tempfile.TemporaryDirectory() as d:
        for f in REQUIRED_FILES:
            _write(d, f, "{}")
        _write(d, "copy-contract.json", '{"slots": [')
        r = lint_template(d)
        assert not r.healthy
        assert any("copy-contract.json:1:" in f.message
                   for f in r.findings if f.severity == "error")


def test_lint_report_serialization():
    with tempfile.TemporaryDirectory() as d:
        r = lint_template(d)
        dct = r.to_dict()
        assert dct["healthy"] is False
        assert dct["summary"]["errors"] > 0
        md = r.to_markdown()
        assert "lint 报告" in md


def test_lint_not_a_dir():
    with pytest.raises(ValueError):
        lint_template("C:/no/such/dir-xyz")
