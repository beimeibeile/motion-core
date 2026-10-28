# -*- coding: utf-8 -*-
"""Tests for core.producer (Phase 3 I1/I2/I3)."""
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
