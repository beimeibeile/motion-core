# -*- coding: utf-8 -*-
"""Producer module: task list management for batch video production (Phase 3 I1-I5)."""
from .param_fill import ParamFiller, substitute, fill, collect_row_vars
from .report import BatchQualityReport, build_report, RETRYABLE_STAGES
from .runner import ProducerRunner, RunSummary
from .template_index import TemplateIndex, TemplateProfile
from .task_manager import (
    ProducerTask,
    TaskManager,
    STATUS_PENDING,
    STATUS_READY,
    STATUS_RUNNING,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_SKIPPED,
)

__all__ = [
    "ProducerTask",
    "TaskManager",
    "ProducerRunner",
    "RunSummary",
    "ParamFiller",
    "substitute",
    "fill",
    "collect_row_vars",
    "TemplateIndex",
    "TemplateProfile",
    "BatchQualityReport",
    "build_report",
    "RETRYABLE_STAGES",
    "STATUS_PENDING",
    "STATUS_READY",
    "STATUS_RUNNING",
    "STATUS_DONE",
    "STATUS_FAILED",
    "STATUS_SKIPPED",
]
