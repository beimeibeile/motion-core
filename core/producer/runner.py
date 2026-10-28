# -*- coding: utf-8 -*-
"""I3 并行渲染调度：ProducerRunner 任务状态机编排 + 断点续跑 + 摘要。

把 TaskManager 的任务列表接入 BatchScheduler：
  ready 任务 → BatchTask（解析模板路径/输出路径）→ 状态 running → 渲染+质量门
  → done / failed（error/attempts/耗时回写）→ 落盘

断点续跑：run 只取 ready 状态任务；done/failed 不重跑；
--retry-failed 时 failed 任务重置 ready 后重跑；resume=True 跳过输出已存在。
"""
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .task_manager import TaskManager, ProducerTask, STATUS_READY, \
    STATUS_RUNNING, STATUS_DONE, STATUS_FAILED
from ..renderer.batch import BatchScheduler, BatchTask


@dataclass
class RunSummary:
    """一次 run 的汇总。"""
    ran: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped_existing: int = 0
    skipped_by_status: int = 0
    elapsed_s: float = 0.0
    failures: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ran": self.ran,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "skipped_existing": self.skipped_existing,
            "skipped_by_status": self.skipped_by_status,
            "elapsed_s": round(self.elapsed_s, 2),
            "failures": self.failures,
        }


class ProducerRunner:
    """批量生产执行器：任务状态机 ↔ 渲染调度器联动。

    Args:
        manager: TaskManager 实例（任务持久化）
        scheduler_factory: 返回 BatchScheduler 的工厂（测试可注入 fake）
        resume: 输出已存在的任务跳过（断点续跑）
    """

    def __init__(self, manager: TaskManager,
                 scheduler_factory: Optional[Callable[[], BatchScheduler]] = None,
                 resume: bool = True):
        self.mgr = manager
        self.scheduler_factory = scheduler_factory or self._default_factory
        self.resume = resume

    @staticmethod
    def _default_factory() -> BatchScheduler:
        return BatchScheduler(jobs=1, retry=1, resume=True)

    def run(self, retry_failed: bool = False,
            progress_cb: Optional[Callable] = None) -> RunSummary:
        """执行 ready 任务（retry_failed 时含 failed 重置）。

        返回 RunSummary；任务状态与结果回写 tasks.json。
        """
        summary = RunSummary()
        t0 = time.time()

        # 候选：ready（+ 可选 failed 重置）
        candidates = [t for t in self.mgr.list_tasks()
                      if t.status == STATUS_READY]
        if retry_failed:
            failed = [t for t in self.mgr.list_tasks()
                      if t.status == STATUS_FAILED]
            for t in failed:
                t.status = STATUS_READY
                t.error = ""
            candidates.extend(failed)
        summary.skipped_by_status = (len(self.mgr.list_tasks())
                                     - len(candidates))
        if not candidates:
            summary.elapsed_s = time.time() - t0
            return summary

        # 解析路径 + 标记 running + 落盘
        batch_tasks = []
        for t in candidates:
            path = self.mgr.resolve_template(t.template)
            if path is None:
                t.status = STATUS_FAILED
                t.error = f"模板不存在: {t.template}"
                summary.failed += 1
                summary.failures.append({"id": t.id, "error": t.error})
                continue
            out = self.mgr.resolve_output(t)
            if not out:
                out = str(Path(t.template).stem + ".mp4")
            t.status = STATUS_RUNNING
            t.error = ""
            batch_tasks.append(BatchTask(str(path), t.params, out, name=t.id))
        self.mgr.save()

        # 渲染（resume 跳过输出已存在）
        scheduler = self.scheduler_factory()
        scheduler.resume = self.resume
        report = scheduler.run(batch_tasks, progress_cb=progress_cb)
        by_output = {r.output_path: r for r in report.tasks}

        # 状态回写（BatchTask.name 即任务 id）
        for t in batch_tasks:
            result = by_output.get(t.output_path)
            task = self.mgr.get(t.name)
            if task is None:
                continue
            if result is None:  # resume 跳过（输出已存在 → 视为完成，不再重跑）
                summary.skipped_existing += 1
                task.status = STATUS_DONE
                task.meta["skipped_existing"] = True
                task.error = ""
                continue
            task.status = STATUS_DONE if result.success else STATUS_FAILED
            task.error = result.error if not result.success else ""
            task.attempts = result.attempts
            task.meta["elapsed_s"] = result.elapsed_s
            task.meta["output"] = result.output_path
            if not result.success:
                task.meta["stage"] = result.error_stage
            if result.success:
                summary.succeeded += 1
            else:
                summary.failed += 1
                summary.failures.append(
                    {"id": t.name, "stage": result.error_stage,
                     "error": result.error[:200]})
        self.mgr.save()

        summary.ran = len(batch_tasks) - summary.skipped_existing
        summary.elapsed_s = time.time() - t0
        return summary
