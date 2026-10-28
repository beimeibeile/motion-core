# -*- coding: utf-8 -*-
"""Tests for core.producer.task_manager (Phase 3 I1)."""
import json
from pathlib import Path

import pytest

from core.producer import (
    TaskManager,
    STATUS_READY,
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
