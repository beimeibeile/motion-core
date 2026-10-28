# -*- coding: utf-8 -*-
"""I5 批量质量报告：聚合渲染结果、异常分类与重试策略建议。

数据源：TaskManager.tasks.json（status/error/attempts/meta.elapsed_s）。

报告维度：
  - 汇总：各状态任务数 + 成功率 + 平均耗时
  - 阶段分布：error_stage（env/prepare/render/gate/unknown）
  - 异常分类：stage + 错误样例聚合
  - 重试策略：可重试（env/prepare/render/unknown → --retry-failed 重跑）
             不可重试（gate 质量门拦截 / 模板缺失 → 需人工或修复）
"""
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from .task_manager import TaskManager
from ..renderer.batch import BatchScheduler

# 可自动重试的失败阶段（与 BatchScheduler.RETRYABLE_STAGES 对齐）
RETRYABLE_STAGES = BatchScheduler.RETRYABLE_STAGES


@dataclass
class TaskReportRow:
    """单个任务的报告行。"""
    id: str
    status: str
    template: str
    output: str
    elapsed_s: Optional[float] = None
    attempts: int = 0
    stage: str = ""
    error: str = ""
    retryable: bool = False


@dataclass
class BatchQualityReport:
    """批量渲染质量报告。"""
    generated_at: str = ""
    tasks_file: str = ""
    total: int = 0
    by_status: dict = field(default_factory=dict)
    success_rate: float = 0.0
    avg_elapsed_s: float = 0.0
    stage_breakdown: dict = field(default_factory=dict)
    error_types: list[dict] = field(default_factory=list)
    retryable_ids: list[str] = field(default_factory=list)
    blocked_ids: list[str] = field(default_factory=list)
    tasks: list[TaskReportRow] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "tasks_file": self.tasks_file,
            "summary": {
                "total": self.total,
                "by_status": self.by_status,
                "success_rate": round(self.success_rate, 2),
                "avg_elapsed_s": round(self.avg_elapsed_s, 2),
            },
            "stage_breakdown": self.stage_breakdown,
            "error_types": self.error_types,
            "retry_strategy": {
                "retryable_ids": self.retryable_ids,
                "blocked_ids": self.blocked_ids,
            },
            "tasks": [{
                "id": r.id, "status": r.status, "template": r.template,
                "output": r.output, "elapsed_s": r.elapsed_s,
                "attempts": r.attempts, "stage": r.stage,
                "error": r.error, "retryable": r.retryable,
            } for r in self.tasks],
        }

    def to_markdown(self) -> str:
        s = self.by_status
        lines = [
            "# 批量渲染质量报告",
            "",
            f"- 生成时间：{self.generated_at}",
            f"- 任务文件：{self.tasks_file}",
            f"- 总数：{self.total}｜完成 {s.get('done', 0)}｜失败 "
            f"{s.get('failed', 0)}｜跳过 {s.get('skipped', 0)}｜"
            f"待执行 {s.get('ready', 0) + s.get('pending', 0)}",
            f"- 成功率：{self.success_rate:.0%}｜平均耗时："
            f"{self.avg_elapsed_s:.1f}s",
            "",
            "## 阶段分布",
            "",
        ]
        lines += [f"- {k}: {v}" for k, v in sorted(
            self.stage_breakdown.items(), key=lambda x: -x[1])]
        if self.error_types:
            lines += ["", "## 异常分类", ""]
            for e in self.error_types:
                lines.append(f"- [{e['stage']}] ×{e['count']}: "
                             f"{e['sample_error'][:100]}")
        if self.retryable_ids:
            lines += ["", f"## 可重试（{len(self.retryable_ids)} 个）",
                      "",
                      "```bash",
                      f"python producer.py run --retry-failed "
                      f"--tasks-file {self.tasks_file}",
                      "```",
                      ""]
        if self.blocked_ids:
            lines += ["", f"## 需人工处理（{len(self.blocked_ids)} 个）", ""]
            for r in self.tasks:
                if r.id in self.blocked_ids:
                    lines.append(f"- {r.id} [{r.stage}]: {r.error[:100]}")
        return "\n".join(lines)


def build_report(manager: TaskManager) -> BatchQualityReport:
    """从 TaskManager 聚合批量质量报告。"""
    report = BatchQualityReport(
        generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        tasks_file=str(manager.tasks_file),
    )
    tasks = manager.list_tasks()
    report.total = len(tasks)
    report.by_status = dict(Counter(t.status for t in tasks))

    done = [t for t in tasks if t.status == "done"]
    report.success_rate = (len(done) / report.total
                           if report.total else 0.0)
    elaps = [t.meta.get("elapsed_s", 0) for t in done
             if isinstance(t.meta.get("elapsed_s"), (int, float))]
    report.avg_elapsed_s = (sum(elaps) / len(elaps) if elaps else 0.0)

    stages = Counter()
    err_counts: Counter = Counter()
    err_sample: dict = {}
    for t in tasks:
        stage = ""
        if t.status == "failed":
            stage = t.meta.get("stage", "") or _infer_stage(t.error)
            stages[stage] += 1
            key = (stage, t.error[:50])
            err_counts[key] += 1
            err_sample.setdefault(key, t.error)
        retryable = t.status == "failed" and (
            stage in RETRYABLE_STAGES)
        report.tasks.append(TaskReportRow(
            id=t.id, status=t.status, template=t.template,
            output=t.output, elapsed_s=t.meta.get("elapsed_s"),
            attempts=t.attempts, stage=stage, error=t.error,
            retryable=retryable))
        if retryable:
            report.retryable_ids.append(t.id)
        elif t.status == "failed":
            report.blocked_ids.append(t.id)

    report.stage_breakdown = dict(stages)
    report.error_types = [
        {"stage": k[0], "count": v, "sample_error": err_sample[k]}
        for k, v in err_counts.most_common()]
    return report


def _infer_stage(error: str) -> str:
    """从错误文本推断失败阶段（兼容无 stage 元数据的旧任务）。"""
    if not error:
        return "unknown"
    if "模板不存在" in error or "找不到" in error:
        return "prepare"
    if "质量" in error or "gate" in error or "loudness" in error \
            or "ffprobe" in error:
        return "gate"
    return "render"
