"""I5 批量质量报告 + OpenFilm P3 并入：doctor / lint / contact sheet 集成测试。"""

import json
import os
import subprocess
import tempfile

from core.producer import build_report
from core.producer.task_manager import TaskManager


def _lavfi_video(path: str) -> None:
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi",
           "-i", "testsrc2=size=160x90:rate=10:duration=1",
           "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-y", path]
    subprocess.run(cmd, capture_output=True, timeout=60)


def _template_dir(tmp: str) -> str:
    d = os.path.join(tmp, "tpl")
    os.makedirs(os.path.join(d, "native"))
    with open(os.path.join(d, "motion.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"id": "t", "name": "T", "version": "1.0",
                            "runtime": "html-canvas/1"}))
    with open(os.path.join(d, "params.schema.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    with open(os.path.join(d, "guide.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    return d


def _bad_template_dir(tmp: str) -> str:
    d = os.path.join(tmp, "badtpl")
    os.makedirs(d)
    with open(os.path.join(d, "motion.json"), "w", encoding="utf-8") as f:
        f.write('{"broken":')
    return d


def _task_file(tmp: str, entries: list[dict]) -> str:
    p = os.path.join(tmp, "tasks.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)
    return p


def test_build_report_doctor_present():
    """build_report 默认含 doctor 环境自检字段。"""
    with tempfile.TemporaryDirectory() as tmp:
        tpl = _template_dir(tmp)
        vid = os.path.join(tmp, "a.mp4")
        _lavfi_video(vid)
        tf = _task_file(tmp, [
            {"id": "t1", "template": tpl, "output": vid,
             "status": "done", "attempts": 1,
             "meta": {"elapsed_s": 1.5}}])
        mgr = TaskManager(tasks_file=tf, template_dir=tmp)
        report = build_report(mgr)
        dct = report.to_dict()
        assert "environment" in dct
        assert dct["environment"]["summary"]["environment"]["ok"] >= 1
        assert "env_warnings" in dct


def test_build_report_lint_healthy_and_bad():
    """lint：好模板 healthy、坏模板 errors>0、.motion 文件形态跳过。"""
    with tempfile.TemporaryDirectory() as tmp:
        tpl = _template_dir(tmp)
        bad = _bad_template_dir(tmp)
        vid = os.path.join(tmp, "a.mp4")
        _lavfi_video(vid)
        tf = _task_file(tmp, [
            {"id": "ok", "template": tpl, "output": vid,
             "status": "done", "attempts": 1},
            {"id": "bad", "template": bad, "output": os.path.join(tmp, "b.mp4"),
             "status": "failed", "attempts": 1,
             "error": "render boom"}])
        mgr = TaskManager(tasks_file=tf, template_dir=tmp)
        report = build_report(mgr, with_contact_sheet=False)
        checks = {c["id"]: c for c in report.template_checks}
        assert checks["ok"]["healthy"] is True
        assert checks["bad"]["healthy"] is False
        assert checks["bad"]["errors"] >= 1


def test_build_report_contact_sheet_generated():
    """done 成片自动生成 contact-sheet.png，路径入报告。"""
    with tempfile.TemporaryDirectory() as tmp:
        tpl = _template_dir(tmp)
        vid = os.path.join(tmp, "a.mp4")
        _lavfi_video(vid)
        tf = _task_file(tmp, [
            {"id": "t1", "template": tpl, "output": vid,
             "status": "done", "attempts": 1,
             "meta": {"elapsed_s": 1.5}}])
        mgr = TaskManager(tasks_file=tf, template_dir=tmp)
        report = build_report(mgr)
        assert len(report.contact_sheets) == 1
        cs = report.contact_sheets[0]
        assert cs["id"] == "t1"
        assert "error" not in cs
        assert cs["frames"] >= 1
        assert os.path.isfile(cs["path"])
        assert cs["path"].endswith(".contact-sheet.png")


def test_build_report_switches_off():
    """--no-* 等价：三项增强全部关闭后字段缺失。"""
    with tempfile.TemporaryDirectory() as tmp:
        tpl = _template_dir(tmp)
        vid = os.path.join(tmp, "a.mp4")
        _lavfi_video(vid)
        tf = _task_file(tmp, [
            {"id": "t1", "template": tpl, "output": vid,
             "status": "done", "attempts": 1}])
        mgr = TaskManager(tasks_file=tf, template_dir=tmp)
        report = build_report(mgr, with_doctor=False, with_lint=False,
                              with_contact_sheet=False)
        dct = report.to_dict()
        assert "environment" not in dct
        assert "template_checks" not in dct
        assert "contact_sheets" not in dct
        # 基础聚合仍在
        assert dct["summary"]["total"] == 1


def test_build_report_contact_sheet_missing_output():
    """done 但成片缺失：contact sheet 记录 error 不阻断。"""
    with tempfile.TemporaryDirectory() as tmp:
        tpl = _template_dir(tmp)
        tf = _task_file(tmp, [
            {"id": "t1", "template": tpl, "output": "gone.mp4",
             "status": "done", "attempts": 1}])
        mgr = TaskManager(tasks_file=tf, template_dir=tmp)
        report = build_report(mgr, with_lint=False)
        assert report.contact_sheets[0]["error"] == "成片不存在"


def test_build_report_markdown_contains_sections():
    """markdown 含 P3 段标题。"""
    with tempfile.TemporaryDirectory() as tmp:
        tpl = _template_dir(tmp)
        vid = os.path.join(tmp, "a.mp4")
        _lavfi_video(vid)
        tf = _task_file(tmp, [
            {"id": "t1", "template": tpl, "output": vid,
             "status": "done", "attempts": 1}])
        mgr = TaskManager(tasks_file=tf, template_dir=tmp)
        report = build_report(mgr)
        md = report.to_markdown()
        assert "环境自检（doctor）" in md
        assert "模板结构 lint" in md
        assert "contact sheet" in md
