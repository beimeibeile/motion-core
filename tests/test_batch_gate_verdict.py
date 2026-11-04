"""I8 批量验证暴露的真实缺陷：并发工作目录冲突 + 质量门 WARN 误判失败。

覆盖三类回归：
  1. 同一模板多次渲染必须落在不同工作目录（否则并发互相踩踏）；
  2. 质量门 WARN（通过但有提醒）算成功，且提醒要能被报告看见；
  3. 质量门 FAIL / 渲染失败必须给出非空原因，不能只报「失败」。
"""

import os

from core.renderer import batch as batch_mod
from core.renderer.batch import BatchScheduler, BatchTask
from core.renderer.pipeline import RenderPipeline


class _FakeStatus:
    def __init__(self, value):
        self.value = value


class _FakeGateReport:
    def __init__(self, status, checks=None):
        self.status = _FakeStatus(status)
        self._checks = checks or []

    def to_dict(self):
        return {"status": self.status.value, "checks": self._checks,
                "summary": {}}


class _FakeGate:
    status_value = "pass"
    checks = []

    def __init__(self, config=None):
        pass

    def check(self, *a, **kw):
        return _FakeGateReport(self.status_value, self.checks)


class _FakePipeline:
    render_result = {"success": True, "width": 1920, "height": 1080}

    def __init__(self, *a, **kw):
        self.kwargs = kw

    def render(self, params, output_path, cleanup=True):
        return dict(self.render_result)


def _task(tmp_path, name="t1"):
    tpl = tmp_path / "tpl.motion"
    tpl.write_bytes(b"")
    return BatchTask(str(tpl), {"duration": 8}, str(tmp_path / "out.mp4"), name=name)


# ---------- 1. 并发工作目录必须独占 ----------

def test_instance_dir_unique_per_render(tmp_path, monkeypatch):
    """两次渲染同一模板，工作目录不能相同。"""
    monkeypatch.setattr(batch_mod, "RenderPipeline", _FakePipeline)
    s = BatchScheduler(jobs=4, work_dir=str(tmp_path / "_render_work"))
    tags = [s._instance_tag(_task(tmp_path, f"job{i}")) for i in range(10)]
    assert len(set(tags)) == 10, "并发实例后缀必须互不相同"


def test_instance_tag_keeps_task_name_readable(tmp_path):
    """后缀保留任务名，便于排查是哪个任务留下的目录。"""
    t = _task(tmp_path, "酷炫科技风/001")
    tag = BatchScheduler._instance_tag(t)
    assert tag.startswith("酷炫科技风")
    assert "/" not in tag and "\\" not in tag


def test_pipeline_instance_dir_contains_tag(tmp_path, monkeypatch):
    """RenderPipeline 的实例目录 = 工作根 / 模板名-后缀，后缀默认随机。"""
    from core.renderer import pipeline as pipeline_mod
    monkeypatch.setattr(pipeline_mod, "detect_format", lambda p: "standard")
    monkeypatch.setattr(pipeline_mod, "parse_template", lambda p: {})
    tpl = tmp_path / "tpl.motion"
    tpl.write_bytes(b"")
    root = tmp_path / "_render_work"
    p1 = RenderPipeline(str(tpl), work_dir=str(root))
    p2 = RenderPipeline(str(tpl), work_dir=str(root))
    assert p1.instance_tag != p2.instance_tag
    d1 = root / f"{tpl.stem}-{p1.instance_tag}"
    assert d1.parent == root and tpl.stem in d1.name


def test_pipeline_accepts_explicit_tag(tmp_path, monkeypatch):
    from core.renderer import pipeline as pipeline_mod
    monkeypatch.setattr(pipeline_mod, "detect_format", lambda p: "standard")
    monkeypatch.setattr(pipeline_mod, "parse_template", lambda p: {})
    tpl = tmp_path / "tpl.motion"
    tpl.write_bytes(b"")
    root = tmp_path / "_render_work"
    p = RenderPipeline(str(tpl), work_dir=str(root), instance_tag="fixed-tag")
    assert p.instance_tag == "fixed-tag"


# ---------- 2/3. 质量门结论 ----------

def _run(tmp_path, monkeypatch, gate_status, checks, render_result):
    monkeypatch.setattr(batch_mod, "RenderPipeline", _FakePipeline)
    monkeypatch.setattr(batch_mod, "QualityGate", _FakeGate)
    _FakeGate.status_value = gate_status
    _FakeGate.checks = checks
    _FakePipeline.render_result = render_result
    s = BatchScheduler(jobs=1, retry=0)
    return s._run_task(_task(tmp_path))


def test_gate_warn_counts_as_success(tmp_path, monkeypatch):
    """WARN = 成品可用但有提醒，不能判失败；提醒项要带出来。"""
    checks = [{"name": "clock_risk", "status": "warn", "message": "时钟风险"},
              {"name": "frame_static", "status": "warn", "message": "静止帧"}]
    r = _run(tmp_path, monkeypatch, "warn", checks,
             {"success": True, "width": 1920, "height": 1080})
    assert r.success is True
    assert r.gate["status"] == "warn"
    warns = [c["name"] for c in r.gate["checks"] if c["status"] == "warn"]
    assert warns == ["clock_risk", "frame_static"]


def test_gate_fail_names_the_bad_check(tmp_path, monkeypatch):
    """FAIL 必须失败，且 error 里要写明是哪一项不合格。"""
    checks = [{"name": "resolution_mismatch", "status": "fail", "message": "分辨率不符"}]
    r = _run(tmp_path, monkeypatch, "fail", checks,
             {"success": True, "width": 1280, "height": 720})
    assert r.success is False
    assert r.error_stage == "gate"
    assert "resolution_mismatch" in r.error


def test_render_failure_always_has_reason(tmp_path, monkeypatch):
    """渲染失败即使引擎没给原因，error 也不能是空串。"""
    r = _run(tmp_path, monkeypatch, "pass", [],
             {"success": False, "error_stage": "", "error": ""})
    assert r.success is False
    assert r.error.strip(), "失败原因不能为空"


# ---------- 报告必须能看到提醒 ----------

def test_report_surfaces_gate_warn(tmp_path):
    """续跑/批量完成后，报告要能统计出「通过但有提醒」的任务。"""
    from core.producer import build_report
    from core.producer.task_manager import TaskManager

    import json
    tasks = []
    for i in range(3):
        out = tmp_path / f"v{i}.mp4"
        out.write_bytes(b"x")
        tasks.append({"id": f"t{i}", "template": str(tmp_path / "t.motion"),
                      "output": str(out), "status": "done", "attempts": 1,
                      "meta": {"elapsed_s": 1.0, "gate_status": "warn"}})
    tf = tmp_path / "tasks.json"
    tf.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
    report = build_report(TaskManager(tasks_file=str(tf), template_dir=str(tmp_path)),
                          with_doctor=False, with_lint=False,
                          with_contact_sheet=False)
    assert report.gate_summary.get("warn") == 3
    assert set(report.warn_ids) == {"t0", "t1", "t2"}
    md = report.to_markdown()
    assert "通过但有提醒" in md
