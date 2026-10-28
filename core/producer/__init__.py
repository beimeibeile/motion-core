# -*- coding: utf-8 -*-
"""Producer module: task list management for batch video production (Phase 3 I1)."""
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
    "STATUS_PENDING",
    "STATUS_READY",
    "STATUS_RUNNING",
    "STATUS_DONE",
    "STATUS_FAILED",
    "STATUS_SKIPPED",
]
