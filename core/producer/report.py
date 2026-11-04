# -*- coding: utf-8 -*-
"""I5 批量质量报告：聚合渲染结果、异常分类与重试策略建议。

数据源：TaskManager.tasks.json（status/error/attempts/meta.elapsed_s）。

报告维度：
  - 汇总：各状态任务数 + 成功率 + 平均耗时
  - 阶段分布：error_stage（env/prepare/render/gate/unknown）
  - 异常分类：stage + 错误样例聚合
  - 重试策略：可重试（env/prepare/render/unknown → --retry-failed 重跑）
             不可重试（gate 质量门拦截 / 模板缺失 → 需人工或修复）

OpenFilm P3 并入（#61 三项，默认开启、失败容忍、可逐项关闭）：
  - doctor 环境自检 → environment / env_warnings
  - 严格解析 lint → template_checks（逐任务模板结构）
  - contact sheet    → contact_sheets（done 成片 <output>.contact-sheet.png）
"""
import os
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
    # OpenFilm P3 并入字段
    environment: Optional[dict] = None
    env_warnings: list[str] = field(default_factory=list)
    template_checks: list[dict] = field(default_factory=list)
    contact_sheets: list[dict] = field(default_factory=list)
    # 成品检查结论（质量门 WARN 不算失败，但必须在报告里看得见）
    gate_summary: dict = field(default_factory=dict)
    warn_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
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
            "gate_summary": self.gate_summary,
            "warn_ids": self.warn_ids,
        }
        if self.environment is not None:
            d["environment"] = self.environment
            d["env_warnings"] = self.env_warnings
        if self.template_checks:
            d["template_checks"] = self.template_checks
        if self.contact_sheets:
            d["contact_sheets"] = self.contact_sheets
        return d

    @staticmethod
    def _display_path(path: str, base_dir: Optional[str]) -> str:
        """报告给人看，路径按 base_dir 转相对 —— 报告要能随产物一起搬走。"""
        if base_dir and path:
            try:
                return os.path.relpath(path, base_dir).replace(os.sep, "/")
            except (ValueError, OSError):
                pass
        return path

    def to_markdown(self, base_dir: Optional[str] = None) -> str:
        s = self.by_status
        lines = [
            "# 批量渲染质量报告",
            "",
            f"- 生成时间：{self.generated_at}",
            f"- 任务文件：{self._display_path(self.tasks_file, base_dir)}",
            f"- 总数：{self.total}｜完成 {s.get('done', 0)}｜失败 "
            f"{s.get('failed', 0)}｜跳过 {s.get('skipped', 0)}｜"
            f"待执行 {s.get('ready', 0) + s.get('pending', 0)}",
            f"- 成功率：{self.success_rate:.0%}｜平均耗时："
            f"{self.avg_elapsed_s:.1f}s",
        ]
        if self.stage_breakdown:
            lines += ["", "## 阶段分布", ""]
            lines += [f"- {k}: {v}" for k, v in sorted(
                self.stage_breakdown.items(), key=lambda x: -x[1])]
        if self.gate_summary:
            lines += ["", "## 成品检查结论", ""]
            label = {"pass": "全部通过", "warn": "通过但有提醒",
                     "unchecked": "未复核（续跑跳过）"}
            for key in ("pass", "warn", "unchecked"):
                if self.gate_summary.get(key):
                    lines.append(f"- {label[key]}: {self.gate_summary[key]}")
            if self.warn_ids:
                shown = (self.warn_ids if len(self.warn_ids) <= 3
                         else self.warn_ids[:3] + [f"…共 {len(self.warn_ids)} 个"])
                lines.append(f"- 有提醒项的任务：{'、'.join(shown)}")
                lines.append("  （提醒不阻断出片，需人工确认；逐项明细见 JSON 报告）")
        if self.error_types:
            lines += ["", "## 异常分类", ""]
            for e in self.error_types:
                lines.append(f"- [{e['stage']}] ×{e['count']}: "
                             f"{e['sample_error'][:100]}")
        if self.environment is not None:
            env = self.environment.get("summary", {}).get("environment", {})
            lines += ["", "## 环境自检（doctor）", ""]
            for k, v in env.items():
                lines.append(f"- {k}: {v}")
            if self.env_warnings:
                lines.append("")
                for w in self.env_warnings:
                    lines.append(f"- ⚠️ {w}")
        if self.template_checks:
            lines += ["", "## 模板结构 lint", ""]
            bad = [c for c in self.template_checks if not c["healthy"]]
            lines.append(f"- 通过 {len(self.template_checks) - len(bad)} / "
                         f"共 {len(self.template_checks)}")
            for c in bad:
                lines.append(f"- ❌ {c['id']}（{c.get('template', '')}）: "
                             f"{c.get('errors', 0)} errors")
        if self.contact_sheets:
            lines += ["", "## contact sheet（O2 验收可视化附件）", ""]
            for c in self.contact_sheets:
                frames = c.get("error") or c.get("frames", "?")
                path = c.get("path", "")
                lines.append(f"- {c['id']}（{frames} 帧）")
                if path and os.path.isfile(path):
                    ref = (os.path.relpath(path, base_dir) if base_dir else path)
                    lines += ["", f"![{c['id']}]({ref.replace(os.sep, '/')})"]
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


def _collect_environment() -> dict:
    """doctor 环境自检：node/ffmpeg/ffprobe/Chrome + 模板完整性。"""
    from ..doctor import run_doctor
    doc = run_doctor()
    dct = doc.to_dict()
    warnings = []
    names = {i["name"]: i for i in dct.get("environment", [])}
    for required in ("node", "ffmpeg", "ffprobe"):
        if required in names and names[required]["status"] == "missing":
            warnings.append(f"{required} 缺失：渲染/抽帧受影响，请先安装")
    if not dct.get("healthy"):
        warnings.append("环境自检未通过，渲染结果可能不可靠")
    return {"environment": dct, "warnings": warnings}


def _collect_lints(manager: TaskManager) -> list[dict]:
    """逐任务模板结构 lint（目录形态才 lint；.motion 文件跳过并标注）。"""
    from ..template_parser.strict import lint_template
    checks: list[dict] = []
    for t in manager.list_tasks():
        resolved = manager.resolve_template(t.template)
        if not resolved:
            checks.append({"id": t.id, "template": t.template,
                           "healthy": False, "errors": 1,
                           "error": "模板路径无法解析"})
            continue
        if resolved.is_file():
            checks.append({"id": t.id, "template": t.template,
                           "healthy": True, "errors": 0,
                           "note": ".motion 文件形态，lint 跳过（解包后另检）"})
            continue
        try:
            r = lint_template(str(resolved))
            checks.append({"id": t.id, "template": t.template,
                           "healthy": r.healthy,
                           "errors": r.summary.get("errors", 0),
                           "warnings": r.summary.get("warnings", 0)})
        except Exception as e:
            checks.append({"id": t.id, "template": t.template,
                           "healthy": False, "errors": 1,
                           "error": str(e)[:200]})
    return checks


def _collect_contact_sheets(manager: TaskManager,
                            n: int = 12) -> list[dict]:
    """done 成片生成 contact sheet（失败容忍，不阻断报告）。"""
    from ..quality_gate.contact_sheet import generate_contact_sheet
    sheets: list[dict] = []
    for t in manager.list_tasks():
        if t.status != "done":
            continue
        out = manager.resolve_output(t)
        if not out or not os.path.isfile(out):
            sheets.append({"id": t.id, "error": "成片不存在"})
            continue
        sheet_path = f"{out}.contact-sheet.png"
        try:
            r = generate_contact_sheet(out, sheet_path, n)
            if "error" in r:
                sheets.append({"id": t.id, "error": r["error"]})
            else:
                sheets.append({"id": t.id, "path": sheet_path,
                               "frames": r["frames"],
                               "duration": r["duration"]})
        except Exception as e:
            sheets.append({"id": t.id, "error": str(e)[:200]})
    return sheets


def build_report(manager: TaskManager,
                 with_doctor: bool = True,
                 with_lint: bool = True,
                 with_contact_sheet: bool = True,
                 contact_sheet_frames: int = 12) -> BatchQualityReport:
    """从 TaskManager 聚合批量质量报告。

    OpenFilm P3 并入（#61）：doctor 环境自检 / 逐任务 lint /
    done 成片 contact sheet，默认开启、失败容忍、可逐项关闭。
    """
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

    # 成品检查结论：WARN 计入 done，但必须能被看见
    gates = Counter(t.meta.get("gate_status") for t in done)
    report.gate_summary = {k: v for k, v in gates.items() if k}
    report.warn_ids = [t.id for t in done
                       if t.meta.get("gate_status") == "warn"]

    if with_doctor:
        env = _collect_environment()
        report.environment = env["environment"]
        report.env_warnings = env["warnings"]
    if with_lint:
        report.template_checks = _collect_lints(manager)
    if with_contact_sheet:
        report.contact_sheets = _collect_contact_sheets(
            manager, n=contact_sheet_frames)
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
