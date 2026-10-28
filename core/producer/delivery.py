# -*- coding: utf-8 -*-
"""I6 成品交付与归档：输出目录规范 + manifest + 源参数归档。

规范：
  输出根目录（默认 <模板目录>/../03-渲染输出）
    ├─ manifest.json            成品清单（任务/模板/参数/路径/耗时/质量门）
    └─ 成品/{YYYYMMDD}/{name}.mp4   归档成品（按日期分目录）
        （源参数内嵌 manifest.items[].params，不散文件）

命名规则：归档名 = 任务输出文件名（保真）；manifest 记录 id/模板/输出。
"""
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .task_manager import TaskManager, STATUS_DONE


@dataclass
class DeliverResult:
    """交付/归档结果。"""
    generated_at: str = ""
    out_root: str = ""
    manifest_path: str = ""
    archived: list[dict] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "out_root": self.out_root,
            "manifest_path": self.manifest_path,
            "archived": self.archived,
            "missing": self.missing,
            "summary": self.summary,
        }


def deliver(manager: TaskManager, out_root: Optional[str] = None,
            archive: bool = True, day_dir: bool = True) -> DeliverResult:
    """归档 done 任务成品并生成 manifest。

    Args:
        manager: TaskManager（done 任务的 resolve_output 指向成品文件）
        out_root: 输出根目录（默认 manager.output_dir 或 "03-渲染输出"）
        archive: 复制成品到归档目录（False 仅生成 manifest）
        day_dir: 归档按日期分目录 成品/{YYYYMMDD}/

    Returns:
        DeliverResult（archived 列表 / missing 缺失文件 / summary）
    """
    result = DeliverResult(generated_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    root = Path(out_root) if out_root else (
        manager.output_dir or Path(manager.tasks_file).parent / "03-渲染输出")
    root.mkdir(parents=True, exist_ok=True)
    result.out_root = str(root)

    day = time.strftime("%Y%m%d")
    arc_dir = (root / "成品" / day) if day_dir else (root / "成品")
    if archive:
        arc_dir.mkdir(parents=True, exist_ok=True)

    items = []
    for t in manager.list_tasks():
        if t.status != STATUS_DONE:
            continue
        src = Path(manager.resolve_output(t))
        entry = {
            "id": t.id, "template": t.template, "name": t.name,
            "output": t.output, "params": t.params,
            "elapsed_s": t.meta.get("elapsed_s"),
            "stage": t.meta.get("stage", ""),
            "attempts": t.attempts,
        }
        if not src.exists():
            result.missing.append(t.id)
            entry["archived"] = False
            items.append(entry)
            continue
        if archive:
            dest = arc_dir / src.name
            shutil.copy2(str(src), str(dest))
            entry["archive_path"] = str(dest)
            entry["archived"] = True
            result.archived.append(entry)
        else:
            entry["archived"] = False
        items.append(entry)

    manifest = {
        "generated_at": result.generated_at,
        "tasks_file": str(manager.tasks_file),
        "out_root": result.out_root,
        "archive_dir": str(arc_dir) if archive else None,
        "items": items,
        "summary": {
            "done": len(items),
            "archived": len(result.archived),
            "missing": len(result.missing),
        },
    }
    result.summary = manifest["summary"]
    mp = root / "manifest.json"
    mp.write_text(__import__("json").dumps(manifest, ensure_ascii=False,
                                           indent=2), encoding="utf-8")
    result.manifest_path = str(mp)
    return result
