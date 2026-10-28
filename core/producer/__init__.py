# -*- coding: utf-8 -*-
"""Producer module: task list management for batch video production (Phase 3 I1-I3)."""
from .param_fill import ParamFiller, substitute, fill, collect_row_vars
from .runner import ProducerRunner, RunSummary
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
    "STATUS_PENDING",
    "STATUS_READY",
    "STATUS_RUNNING",
    "STATUS_DONE",
    "STATUS_FAILED",
    "STATUS_SKIPPED",
]
