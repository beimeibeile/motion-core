"""Batch scheduler: parallel rendering with retry and aggregated quality report.

设计（共享核心库能力）:
  - 任务队列: (template, params, output_path)
  - 并发控制: ThreadPoolExecutor（渲染子进程本身并行，本机渲染建议 jobs<=2）
  - 失败重试: 每任务最多 retry 次，仅重试"可重试"失败
  - 质量门: 每任务 RenderPipeline.render + QualityGate.check（4 层 18 项）
  - 汇总报告: 成功/失败/警告/耗时/错误明细
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .pipeline import RenderPipeline
from ..quality_gate import QualityGate
from ..template_parser import detect_format


@dataclass
class BatchTask:
    """单个渲染任务。"""
    template_path: str
    params: dict
    output_path: str
    name: str = ""

    def __post_init__(self):
        if not self.name:
            self.name = Path(self.template_path).stem


@dataclass
class TaskResult:
    """单个任务结果。"""
    name: str
    template_path: str
    output_path: str
    success: bool
    render: dict = field(default_factory=dict)
    gate: dict = field(default_factory=dict)
    attempts: int = 1
    error_stage: str = ""
    error: str = ""
    elapsed_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "template_path": self.template_path,
            "output_path": self.output_path,
            "success": self.success,
            "render": self.render,
            "gate": self.gate,
            "attempts": self.attempts,
            "error_stage": self.error_stage,
            "error": self.error,
            "elapsed_s": round(self.elapsed_s, 2),
        }


@dataclass
class BatchReport:
    """批量渲染汇总报告。"""
    tasks: list[TaskResult] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0
    skipped_existing: int = 0

    @property
    def total(self) -> int:
        return len(self.tasks)

    @property
    def succeeded(self) -> int:
        return sum(1 for t in self.tasks if t.success)

    @property
    def failed(self) -> int:
        return sum(1 for t in self.tasks if not t.success)

    @property
    def total_elapsed(self) -> float:
        return self.finished_at - self.started_at

    def summary(self) -> dict:
        return {
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "skipped_existing": self.skipped_existing,
            "total_elapsed_s": round(self.total_elapsed, 2),
        }

    def to_dict(self) -> dict:
        return {
            "summary": self.summary(),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "tasks": [t.to_dict() for t in self.tasks],
        }


class BatchScheduler:
    """批量渲染调度器。

    Args:
        jobs: 并发任务数（默认 1；本机渲染子进程密集，建议 1-2）
        retry: 每任务最大重试次数（默认 1，即失败后重试 1 次）
        gate_config: QualityGate 配置（如 ffprobe_path）
        work_dir: 渲染工作目录（默认每个模板同目录 _render_work）
        resume: 断点续跑——输出文件已存在的任务跳过（不重新渲染），
                计入 BatchReport.skipped_existing
    """

    RETRYABLE_STAGES = {"env", "prepare", "render", "unknown"}

    def __init__(self, jobs: int = 1, retry: int = 1,
                 gate_config: Optional[dict] = None,
                 work_dir: Optional[str] = None,
                 resume: bool = False):
        self.jobs = max(1, jobs)
        self.retry = max(0, retry)
        self.gate_config = gate_config or {}
        self.work_dir = work_dir
        self.resume = resume

    def _run_task(self, task: BatchTask, progress_cb: Optional[Callable] = None) -> TaskResult:
        """执行单个任务（含重试与质量门）。"""
        result = TaskResult(
            name=task.name,
            template_path=task.template_path,
            output_path=task.output_path,
            success=False,
        )
        attempts = 0
        while True:
            attempts += 1
            result.attempts = attempts
            t0 = time.time()
            try:
                if progress_cb:
                    progress_cb(task, "render", attempts)
                pipeline = RenderPipeline(task.template_path, work_dir=self.work_dir)
                render = pipeline.render(task.params, task.output_path, cleanup=True)

                # 质量门（仅渲染成功时）
                gate_report = None
                if render.get("success"):
                    if progress_cb:
                        progress_cb(task, "gate", attempts)
                    gate = QualityGate(config=self.gate_config)
                    expected = {
                        "width": render.get("width"),
                        "height": render.get("height"),
                        "duration": task.params.get("duration"),
                    }
                    gate_report = gate.check(
                        task.template_path, task.params, task.output_path,
                        render, expected, task_id=task.name)
                    gate_dict = gate_report.to_dict()
                else:
                    gate_dict = {}

                result.render = render
                result.gate = gate_dict
                result.error_stage = render.get("error_stage", "")
                result.error = render.get("error", "")
                ok = render.get("success", False) and gate_report is not None \
                    and gate_report.status.value == "pass"
                result.success = ok
                if ok:
                    result.elapsed_s = time.time() - t0
                    return result

                # 失败：判断是否可重试
                if not render.get("success"):
                    stage = render.get("error_stage", "unknown")
                    retryable = stage in self.RETRYABLE_STAGES
                else:
                    # 质量门失败（参数/内容问题）不重试
                    retryable = False
                if attempts >= self.retry + 1 or not retryable:
                    result.elapsed_s = time.time() - t0
                    return result

            except Exception as e:
                result.error_stage = "unknown"
                result.error = str(e)
                if attempts >= self.retry + 1:
                    result.elapsed_s = time.time() - t0
                    return result

        return result

    def run(self, tasks: list[BatchTask],
            progress_cb: Optional[Callable] = None) -> BatchReport:
        """并行执行任务队列，返回汇总报告。

        resume=True 时跳过输出已存在的任务（计入 report.skipped_existing）。
        """
        report = BatchReport()
        report.started_at = time.time()
        runnable = list(tasks)
        if self.resume:
            skipped = [t for t in runnable if Path(t.output_path).exists()]
            report.skipped_existing = len(skipped)
            runnable = [t for t in runnable if not Path(t.output_path).exists()]
        done = 0
        total = len(runnable)

        def _cb_wrapper(task, stage, attempt):
            done_ = done
            if progress_cb:
                progress_cb(task, stage, attempt, done_, total)

        if self.jobs == 1 or len(runnable) <= 1:
            for task in runnable:
                report.tasks.append(self._run_task(task, _cb_wrapper))
        else:
            with ThreadPoolExecutor(max_workers=self.jobs) as pool:
                futures = {pool.submit(self._run_task, task, _cb_wrapper): task
                           for task in runnable}
                for fut in as_completed(futures):
                    report.tasks.append(fut.result())

        # 保持任务输入顺序
        order = {t.output_path: i for i, t in enumerate(runnable)}
        report.tasks.sort(key=lambda r: order.get(r.output_path, 0))
        report.finished_at = time.time()
        return report

    @staticmethod
    def save_report(report: BatchReport, path: str) -> None:
        """保存汇总报告 JSON。"""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8")
