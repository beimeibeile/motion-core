"""P3 doctor 环境自检：node/ffmpeg/Chrome 探测 + 模板完整性检查测试。"""

import json
import os
import tempfile

from core.doctor import (DoctorReport, check_chrome, check_ffmpeg, check_node,
                         check_template, run_doctor)
from core.template_parser.strict import lint_template


def _make_template(dirpath: str, bad: bool = False) -> str:
    os.makedirs(dirpath, exist_ok=True)
    with open(os.path.join(dirpath, "motion.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"id": "t", "name": "T", "version": "1.0",
                            "runtime": "html-canvas/1"}))
    with open(os.path.join(dirpath, "params.schema.json"), "w",
              encoding="utf-8") as f:
        f.write("{}" if not bad else '{"p":')
    with open(os.path.join(dirpath, "guide.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    os.makedirs(os.path.join(dirpath, "native"))
    return dirpath


def test_check_node():
    item = check_node()
    assert item.name == "node"
    assert item.status in ("ok", "missing")
    if item.status == "ok":
        assert item.detail.startswith("v")


def test_check_ffmpeg():
    item = check_ffmpeg()
    assert item.name == "ffmpeg"
    assert item.status in ("ok", "missing")


def test_check_chrome_shape():
    item = check_chrome()
    assert item.name == "chrome"
    assert item.status in ("ok", "warn", "missing")


def test_check_template_healthy():
    with tempfile.TemporaryDirectory() as d:
        target = _make_template(d)
        checks = check_template(target)
        assert checks[0].name == "lint"
        assert checks[0].status == "ok"


def test_check_template_bad_json():
    with tempfile.TemporaryDirectory() as d:
        target = _make_template(d, bad=True)
        checks = check_template(target)
        assert checks[0].status == "warn"
        assert "params.schema.json" in checks[0].detail


def test_check_template_missing_dir():
    checks = check_template("C:/no/such/dir-xyz")
    assert checks[0].status == "missing"


def test_run_doctor_with_template():
    with tempfile.TemporaryDirectory() as d:
        target = _make_template(d)
        report = run_doctor(target)
        assert len(report.items) == 4
        assert len(report.template_checks) >= 1
        dct = report.to_dict()
        assert dct["summary"]["environment"]["ok"] >= 1
        assert "node" in [i["name"] for i in dct["environment"]]


def test_run_doctor_without_template():
    report = run_doctor()
    assert report.template_checks == []
    assert "healthy" in report.to_dict()


def test_doctor_report_markdown():
    report = DoctorReport()
    report.items = [check_node(), check_ffmpeg()]
    md = report.to_markdown()
    assert "环境自检报告" in md
    assert "node" in md
