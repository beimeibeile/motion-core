# -*- coding: utf-8 -*-
"""Tests for core.producer (Phase 3 I1-I6)."""
import json
from pathlib import Path

import pytest

from core.producer import (
    TaskManager,
    ProducerRunner,
    ParamFiller,
    substitute,
    fill,
    collect_row_vars,
    TemplateIndex,
    build_report,
    deliver,
    STATUS_READY,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_PENDING,
)


@pytest.fixture
def manager(tmp_path: Path, standard_tpl: Path) -> TaskManager:
    """TaskManager with a real standard template in template_dir."""
    return TaskManager(
        tasks_file=str(tmp_path / "tasks.json"),
        template_dir=str(tmp_path),
        output_dir=str(tmp_path / "out"),
    )


def _write_csv(path: Path, rows: list[dict]) -> Path:
    import csv
    cols = sorted({k for r in rows for k in r})
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return path


class TestTaskManagerCRUD:
    def test_add_list_get(self, manager):
        from conftest import standard_params
        t = manager.add("test_standard", standard_params(), output="a.mp4")
        assert t.id == "test_standard-001"
        assert t.status == STATUS_READY  # 模板存在 + 参数预校验通过
        assert manager.get(t.id) is not None
        assert len(manager.list_tasks()) == 1

    def test_persistence_reload(self, tmp_path, manager):
        from conftest import standard_params
        manager.add("test_standard", standard_params())
        mgr2 = TaskManager(str(tmp_path / "tasks.json"),
                           template_dir=str(tmp_path),
                           output_dir=str(tmp_path / "out"))
        assert len(mgr2.list_tasks()) == 1
        assert mgr2.list_tasks()[0].template == "test_standard"

    def test_update_remove_clear(self, manager):
        from conftest import standard_params
        t = manager.add("test_standard", standard_params())
        manager.update(t.id, name="改名", params={"title": "X"})
        assert manager.get(t.id).name == "改名"
        assert manager.get(t.id).params == {"title": "X"}
        assert manager.remove(t.id) is True
        assert manager.remove(t.id) is False
        assert manager.list_tasks() == []

    def test_update_missing_id(self, manager):
        assert manager.update("nope-000", name="x") is None

    def test_id_unique(self, manager):
        from conftest import standard_params
        t1 = manager.add("test_standard", standard_params())
        t2 = manager.add("test_standard", standard_params())
        assert t1.id != t2.id


class TestTaskManagerImport:
    def test_import_csv_params_expansion(self, manager):
        path = _write_csv(manager.tasks_file.parent / "tasks.csv", [
            {"template": "test_standard", "name": "任务A",
             "output": "a.mp4", "param.mode": "standard",
             "param.language": "zh", "param.layout": "landscape",
             "param.title": "你好", "param.duration": "5.5",
             "param.scenes": '[{"id":"S01","title":"s"}]', "备注": "示例"},
        ])
        imported = manager.import_csv(str(path))
        assert len(imported) == 1
        t = imported[0]
        assert t.params == {"mode": "standard", "title": "你好",
                            "duration": 5.5,
                            "language": "zh", "layout": "landscape",
                            "scenes": [{"id": "S01", "title": "s"}]}
        assert t.meta["备注"] == "示例"
        assert t.status == STATUS_READY

    def test_import_csv_missing_template_col(self, tmp_path):
        path = _write_csv(tmp_path / "bad.csv", [{"title": "x"}])
        with pytest.raises(ValueError):
            TaskManager(str(tmp_path / "tasks.json")).import_csv(str(path))

    def test_import_json(self, manager):
        from conftest import standard_params
        path = manager.tasks_file.parent / "tasks.json.in"
        path.write_text(json.dumps([
            {"template": "test_standard", "params": standard_params(),
             "output": "a.mp4"},
            {"template": "test_standard", "params": standard_params(),
             "output": "b.mp4"},
        ], ensure_ascii=False), encoding="utf-8")
        imported = manager.import_json(str(path))
        assert len(imported) == 2
        assert imported[1].params == standard_params()

    def test_import_json_not_array(self, tmp_path):
        path = tmp_path / "x.json"
        path.write_text('{"template": "a"}', encoding="utf-8")
        with pytest.raises(ValueError):
            TaskManager(str(tmp_path / "tasks.json")).import_json(str(path))


class TestTaskManagerValidate:
    def test_missing_template_skipped(self, manager):
        t = manager.add("不存在的模板")
        assert t.status == STATUS_SKIPPED
        assert "模板不存在" in t.error

    def test_invalid_params_skipped(self, manager):
        # conftest standard schema 必填 mode/language/layout/scenes
        t = manager.add("test_standard", {"title": "只有标题"})
        assert t.status == STATUS_SKIPPED
        assert "schema" in t.error

    def test_valid_params_ready(self, manager, standard_tpl):
        from conftest import standard_params
        t = manager.add("test_standard", standard_params())
        assert t.status == STATUS_READY
        assert t.error == ""

    def test_personal_template_no_schema_ready(self, tmp_path,
                                               personal_tpl_v2):
        mgr = TaskManager(str(tmp_path / "tasks.json"),
                          template_dir=str(tmp_path))
        t = mgr.add("test_personal_v2", {"whatever": 1})
        assert t.status == STATUS_READY
        assert t.meta["template_format"] == "personal-download-v1"


class TestTaskManagerSummary:
    def test_status_summary(self, manager):
        from conftest import standard_params
        manager.add("test_standard", standard_params())   # ready
        manager.add("不存在模板")                          # skipped
        manager.update(manager.list_tasks()[0].id, status="done")
        s = manager.status_summary()
        assert s == {"total": 2, "pending": 0, "ready": 0,
                     "running": 0, "done": 1, "failed": 0, "skipped": 1}

    def test_list_filter(self, manager):
        from conftest import standard_params
        manager.add("test_standard", standard_params())
        manager.add("不存在模板")
        assert len(manager.list_tasks(status=STATUS_READY)) == 1
        assert len(manager.list_tasks(status=STATUS_SKIPPED)) == 1


class TestProducerInvoke:
    def test_invoke_add_list(self, standard_tpl, tmp_path):
        from conftest import standard_params
        from core.invoke import invoke
        tasks_file = str(tmp_path / "tasks.json")
        tdir = str(tmp_path)
        r = invoke("producer_task_manage", action="add", tasks_file=tasks_file,
                   template_dir=tdir, template="test_standard",
                   params=standard_params())
        assert r["success"] is True
        assert r["result"]["task"]["status"] == STATUS_READY
        r2 = invoke("producer_task_manage", action="list",
                    tasks_file=tasks_file, template_dir=tdir)
        assert r2["result"]["summary"]["total"] == 1

    def test_invoke_status_clear(self, standard_tpl, tmp_path):
        from conftest import standard_params
        from core.invoke import invoke
        tasks_file = str(tmp_path / "tasks.json")
        invoke("producer_task_manage", action="add", tasks_file=tasks_file,
               template_dir=str(tmp_path), template="test_standard",
               params=standard_params())
        r = invoke("producer_task_manage", action="status",
                   tasks_file=tasks_file)
        assert r["result"]["ready"] == 1
        invoke("producer_task_manage", action="clear", tasks_file=tasks_file)
        r2 = invoke("producer_task_manage", action="status",
                    tasks_file=tasks_file)
        assert r2["result"]["total"] == 0


class TestParamFiller:
    """I2 参数批量填充。"""

    def test_substitute_basic(self):
        assert substitute("你好 {{name}}", {"name": "世界"}) == "你好 世界"

    def test_substitute_nested_path(self):
        assert substitute("{{a.b}}", {"a": {"b": "深"}}) == "深"

    def test_substitute_default(self):
        assert substitute("{{x|默认}}", {}) == "默认"

    def test_substitute_missing_keep(self):
        assert substitute("{{missing}}", {}, keep_missing=True) == "{{missing}}"
        assert substitute("{{missing}}", {}, keep_missing=False) == ""

    def test_fill_recursive(self):
        data = {"title": "{{t}}", "scenes": [{"id": "{{id}}"}],
                "n": 5, "nested": {"k": "{{k|D}}"}}
        out = fill(data, {"t": "标题", "id": "S1"})
        assert out == {"title": "标题", "scenes": [{"id": "S1"}],
                       "n": 5, "nested": {"k": "D"}}

    def test_fill_task_params(self):
        p = {"title": "{{t}}", "duration": 5}
        out = ParamFiller({"t": "A"}).fill_task(p)
        assert out == {"title": "A", "duration": 5}
        assert p == {"title": "{{t}}", "duration": 5}  # 原对象不变

    def test_collect_row_vars(self):
        row = {"template": "t", "主题": "科技", "a.b": "c", "param.title": "x"}
        assert collect_row_vars(row) == {"主题": "科技", "a": {"b": "c"}}


class TestCsvVarSubstitution:
    """I2 CSV 变量替换（{{列名}} 引用同行字段）。"""

    def test_csv_row_vars(self, manager):
        path = _write_csv(manager.tasks_file.parent / "tasks.csv", [
            {"template": "test_standard", "name": "任务{{n}}",
             "output": "{{主题}}.mp4", "param.mode": "standard",
             "param.language": "zh", "param.layout": "landscape",
             "param.title": "标题-{{主题}}", "param.duration": "{{时长}}",
             "param.scenes": '[{"id":"S01","title":"{{主题}}"}]',
             "主题": "科技", "时长": "6", "n": "3"},
        ])
        imported = manager.import_csv(str(path))
        assert len(imported) == 1
        t = imported[0]
        assert t.name == "任务3"
        assert t.output == "科技.mp4"
        assert t.params["title"] == "标题-科技"
        assert t.params["duration"] == 6
        assert t.params["scenes"] == [{"id": "S01", "title": "科技"}]
        assert t.status == STATUS_READY

    def test_csv_global_vars_file(self, manager, tmp_path):
        vars_file = tmp_path / "vars.json"
        vars_file.write_text(json.dumps({"公司": "某某科技"}), encoding="utf-8")
        path = _write_csv(manager.tasks_file.parent / "tasks.csv", [
            {"template": "test_standard", "param.mode": "standard",
             "param.language": "zh", "param.layout": "landscape",
             "param.title": "{{公司}}宣传", "param.duration": "5",
             "param.scenes": '[{"id":"S01","title":"{{公司}}"}]'},
        ])
        imported = manager.import_csv(str(path), vars_file=str(vars_file))
        assert imported[0].params["title"] == "某某科技宣传"

    def test_json_record_vars(self, manager):
        from conftest import standard_params
        base = standard_params()
        path = manager.tasks_file.parent / "tasks.json.in"
        path.write_text(json.dumps([
            {"template": "test_standard", "params": {
                **base, "title": "{{主题}}-{{n}}"},
             "output": "{{n}}.mp4", "name": "批{{n}}",
             "主题": "新能源", "n": "7"},
        ], ensure_ascii=False), encoding="utf-8")
        imported = manager.import_json(str(path))
        t = imported[0]
        assert t.params["title"] == "新能源-7"
        assert t.name == "批7"
        assert t.output == "7.mp4"


class _FakeScheduler:
    """Fake BatchScheduler 替身：按 (output_path → success) 映射返回结果。"""

    def __init__(self, results: dict, resume: bool = True):
        self.results = results
        self.resume = resume
        self.skipped_existing = 0

    def run(self, tasks, progress_cb=None):
        from core.renderer.batch import TaskResult, BatchReport
        report = BatchReport()
        runnable = list(tasks)
        if self.resume:
            skipped = [t for t in runnable if Path(t.output_path).exists()]
            report.skipped_existing = len(skipped)
            runnable = [t for t in runnable if not Path(t.output_path).exists()]
        for t in runnable:
            ok = self.results.get(t.output_path, False)
            report.tasks.append(TaskResult(
                name=t.name, template_path=t.template_path,
                output_path=t.output_path, success=ok,
                error="" if ok else "fake-fail",
                error_stage="render" if not ok else "",
                elapsed_s=0.1))
        report.finished_at = 1.0
        return report


class TestProducerRunner:
    """I3 并行渲染调度：状态机 / 断点续跑 / 摘要。"""

    @pytest.fixture
    def runner(self, manager):
        return ProducerRunner(manager, resume=True)

    def _add(self, manager, name: str):
        from conftest import standard_params
        # 输出用绝对路径（runner 解析后 = manager.output_dir/name）
        out = str(Path(manager.output_dir) / name)
        t = manager.add("test_standard", standard_params(), output=out)
        assert t.status == STATUS_READY
        return t

    def test_run_success(self, runner, manager):
        self._add(manager, "a.mp4")
        runner.scheduler_factory = lambda: _FakeScheduler(
            {str(Path(manager.output_dir) / "a.mp4"): True})
        s = runner.run()
        assert s.ran == 1 and s.succeeded == 1 and s.failed == 0
        t = manager.get(manager.list_tasks()[0].id)
        assert t.status == STATUS_DONE
        assert t.meta["elapsed_s"] > 0

    def test_run_failure(self, runner, manager):
        self._add(manager, "a.mp4")
        runner.scheduler_factory = lambda: _FakeScheduler(
            {str(Path(manager.output_dir) / "a.mp4"): False})
        s = runner.run()
        assert s.failed == 1 and s.failures[0]["id"].startswith("test_standard")
        t = manager.get(manager.list_tasks()[0].id)
        assert t.status == STATUS_FAILED
        assert t.error == "fake-fail"

    def test_run_skips_done(self, runner, manager):
        # 一个 done + 一个 ready：只跑 ready
        from conftest import standard_params
        t1 = manager.add("test_standard", standard_params(),
                         output=str(Path(manager.output_dir) / "a.mp4"))
        manager.update(t1.id, status=STATUS_DONE)
        t2 = self._add(manager, "b.mp4")
        runner.scheduler_factory = lambda: _FakeScheduler(
            {str(Path(manager.output_dir) / "b.mp4"): True})
        s = runner.run()
        assert s.ran == 1 and s.succeeded == 1
        assert manager.get(t2.id).status == STATUS_DONE
        assert manager.get(t1.id).status == STATUS_DONE  # 不重跑

    def test_retry_failed(self, runner, manager):
        from conftest import standard_params
        t = manager.add("test_standard", standard_params(),
                        output=str(Path(manager.output_dir) / "a.mp4"))
        manager.update(t.id, status=STATUS_FAILED, error="旧错误")
        runner.scheduler_factory = lambda: _FakeScheduler(
            {str(Path(manager.output_dir) / "a.mp4"): True})
        s = runner.run(retry_failed=True)
        assert s.succeeded == 1
        assert manager.get(t.id).status == STATUS_DONE

    def test_run_no_candidates(self, runner, manager):
        s = runner.run()
        assert s.ran == 0 and s.succeeded == 0 and s.failed == 0

    def test_resume_skips_existing(self, manager, tmp_path):
        # 输出文件已存在 → resume 跳过并标记 done
        from conftest import standard_params
        t = manager.add("test_standard", standard_params(),
                        output=str(tmp_path / "exist.mp4"))
        (tmp_path / "exist.mp4").write_bytes(b"ok")
        r = ProducerRunner(manager, resume=True)
        r.scheduler_factory = lambda: _FakeScheduler({}, resume=True)
        s = r.run()
        assert s.ran == 0 and s.skipped_existing == 1
        assert manager.get(t.id).status == STATUS_DONE
        assert manager.get(t.id).meta.get("skipped_existing") is True

    def test_missing_template_failed(self, manager):
        t = manager.add("不存在的模板", {}, output="x.mp4")
        assert t.status == STATUS_SKIPPED
        # 置 failed 后 --retry-failed：runner 重试时模板仍缺失 → failed
        manager.update(t.id, status=STATUS_FAILED, error="旧错误")
        runner = ProducerRunner(manager)
        runner.scheduler_factory = lambda: _FakeScheduler({})
        s = runner.run(retry_failed=True)
        assert s.failed == 1
        assert "模板不存在" in manager.get(t.id).error


class TestTemplateIndex:
    """I4 智能模板选型。"""

    @pytest.fixture
    def tdir(self, tmp_path):
        d = tmp_path / "tpl"
        d.mkdir()
        for name in ["现代图文_口播讲解_横屏_v2.7.11.motion",
                     "现代图文_口播讲解_竖屏_v2.7.11.motion",
                     "酷炫科技风_口播讲解_横屏_v0.2.7.motion",
                     "多屏拼贴_品牌宣传_横屏_v2.6.4.motion"]:
            (d / name).write_bytes(b"x")
        return str(d)

    def test_scan_parses_filenames(self, tdir):
        idx = TemplateIndex(tdir)
        assert len(idx.profiles) == 4
        p = next(x for x in idx.profiles if x.type == "现代图文"
                 and x.aspect == "横屏")
        assert p.purpose == "口播讲解" and p.voiceover is True
        q = next(x for x in idx.profiles if x.purpose == "品牌宣传")
        assert q.voiceover is False

    def test_match_aspect_voiceover(self, tdir):
        idx = TemplateIndex(tdir)
        top = idx.match(aspect="竖屏", voiceover=True, top=2)
        assert top[0].name.startswith("现代图文_口播讲解_竖屏")
        assert top[0].score >= 4

    def test_match_topic(self, tdir):
        idx = TemplateIndex(tdir)
        top = idx.match(topic="科技", aspect="横屏", voiceover=True)
        assert top[0].name.startswith("酷炫科技风") or top[0].score >= 3

    def test_match_duration_range(self, tdir):
        idx = TemplateIndex(tdir)
        # 30s 落入口播类范围 → 现代图文 15-90s 得分
        top = idx.match(duration_s=30, voiceover=True, aspect="横屏")
        assert top[0].name.startswith("现代图文")

    def test_no_match_returns_empty(self, tdir):
        idx = TemplateIndex(tdir)
        # 主题词完全不相关 → 无任何维度命中 → 空推荐
        assert idx.match(topic="完全无关的主题词xyz") == []

    def test_invoke_select(self, tdir):
        from core.invoke import invoke
        r = invoke("producer_template_select", template_dir=tdir,
                   topic="科技", aspect="横屏")
        res = r["result"]
        assert res["total_profiles"] == 4
        assert res["matches"][0]["score"] > 0
        assert any("主题词命中" in r
                   for m in res["matches"] for r in m["reasons"])


class TestBatchReport:
    """I5 批量质量报告。"""

    @pytest.fixture
    def mixed_manager(self, manager):
        from conftest import standard_params
        base = standard_params()
        # done ×2
        t1 = manager.add("test_standard", base, output="a.mp4")
        manager.update(t1.id, status=STATUS_DONE, meta={"elapsed_s": 3.5})
        t2 = manager.add("test_standard", base, output="b.mp4")
        manager.update(t2.id, status=STATUS_DONE, meta={"elapsed_s": 5.0})
        # failed render（可重试）
        t3 = manager.add("test_standard", base, output="c.mp4")
        manager.update(t3.id, status=STATUS_FAILED, error="渲染失败: x",
                       meta={"elapsed_s": 2.0, "stage": "render"})
        # failed gate（不可重试）
        t4 = manager.add("test_standard", base, output="d.mp4")
        manager.update(t4.id, status=STATUS_FAILED, error="质量门 loudness 超标",
                       meta={"stage": "gate"})
        # skipped（模板缺失）
        t5 = manager.add("不存在的模板", {}, output="e.mp4")
        assert t5.status == STATUS_SKIPPED
        return manager

    def test_aggregate(self, mixed_manager):
        r = build_report(mixed_manager)
        assert r.total == 5
        assert r.by_status["done"] == 2
        assert r.by_status["failed"] == 2
        assert r.by_status["skipped"] == 1
        assert r.success_rate == 2 / 5
        assert r.avg_elapsed_s == 4.25

    def test_stage_breakdown(self, mixed_manager):
        r = build_report(mixed_manager)
        assert r.stage_breakdown.get("render") == 1
        assert r.stage_breakdown.get("gate") == 1

    def test_retry_strategy(self, mixed_manager):
        r = build_report(mixed_manager)
        assert len(r.retryable_ids) == 1  # render 可重试
        assert len(r.blocked_ids) == 1    # gate 不可重试
        assert r.error_types[0]["stage"] in ("render", "gate")

    def test_empty_report(self, manager):
        r = build_report(manager)
        assert r.total == 0 and r.success_rate == 0.0
        assert r.retryable_ids == [] and r.blocked_ids == []

    def test_markdown(self, mixed_manager):
        r = build_report(mixed_manager)
        md = r.to_markdown()
        assert "批量渲染质量报告" in md
        assert "可重试" in md or "需人工处理" in md

    def test_invoke_report(self, mixed_manager):
        from core.invoke import invoke
        res = invoke("producer_batch_report",
                     tasks_file=str(mixed_manager.tasks_file))["result"]
        assert res["report"]["summary"]["total"] == 5


class TestDeliver:
    """I6 成品交付与归档。"""

    @pytest.fixture
    def done_manager(self, manager, tmp_path):
        from conftest import standard_params
        out = tmp_path / "out"
        out.mkdir()
        base = standard_params()
        t1 = manager.add("test_standard", base,
                         output=str(out / "成品A.mp4"))
        manager.update(t1.id, status=STATUS_DONE, meta={"elapsed_s": 2.0})
        (out / "成品A.mp4").write_bytes(b"video-A")
        t2 = manager.add("test_standard", base,
                         output=str(out / "成品B.mp4"))
        manager.update(t2.id, status=STATUS_DONE)
        (out / "成品B.mp4").write_bytes(b"video-B")
        # failed 不参与归档
        t3 = manager.add("test_standard", base, output=str(out / "bad.mp4"))
        manager.update(t3.id, status=STATUS_FAILED)
        return manager

    def test_deliver_archives(self, done_manager, tmp_path):
        arc = tmp_path / "arc"
        r = deliver(done_manager, out_root=str(arc))
        assert r.summary["done"] == 2
        assert r.summary["archived"] == 2
        assert r.summary["missing"] == 0
        # 归档目录 成品/{YYYYMMDD}/ 含两个文件
        day = list((arc / "成品").iterdir())[0]
        names = sorted(p.name for p in day.iterdir())
        assert names == ["成品A.mp4", "成品B.mp4"]
        # manifest 存在且含源参数
        m = json.loads((arc / "manifest.json").read_text(encoding="utf-8"))
        assert m["summary"]["archived"] == 2
        assert m["items"][0]["params"]["mode"] == "standard"
        assert m["items"][0]["id"].startswith("test_standard")

    def test_deliver_manifest_only(self, done_manager, tmp_path):
        r = deliver(done_manager, out_root=str(tmp_path / "arc"),
                    archive=False)
        assert r.summary["archived"] == 0
        assert r.summary["done"] == 2
        assert (tmp_path / "arc" / "manifest.json").exists()

    def test_deliver_missing_file(self, manager, tmp_path):
        from conftest import standard_params
        t = manager.add("test_standard", standard_params(),
                        output=str(tmp_path / "no.mp4"))
        manager.update(t.id, status=STATUS_DONE)
        r = deliver(manager, out_root=str(tmp_path / "arc"))
        assert r.summary["missing"] == 1
        assert r.missing == [t.id]

    def test_invoke_deliver(self, done_manager):
        from core.invoke import invoke
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            res = invoke("producer_deliver",
                         tasks_file=str(done_manager.tasks_file),
                         out_root=d)["result"]
            assert res["summary"]["archived"] == 2
