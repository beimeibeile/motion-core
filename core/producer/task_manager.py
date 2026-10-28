# -*- coding: utf-8 -*-
"""Producer task manager: task list persistence + JSON/CSV import.

I1 任务列表管理（Phase 3）：
  - ProducerTask：单个生产任务（模板 + 参数 + 输出 + 状态）
  - TaskManager：tasks.json 持久化（增删改查 / 状态机 / 汇总）
  - 导入器：CSV（param.* 列展开 + 类型转换）与 JSON 任务数组
  - 预校验：模板存在性 + 参数 schema 校验（standard 格式可提取 schema）

状态机：
  pending  （已导入，未校验）
  ready    （校验通过，待运行）
  running  （运行中，I3 设置）
  done     （渲染成功，I3 设置）
  failed   （渲染失败，I3 设置）
  skipped  （预校验失败：模板缺失 / 参数不合 schema）
"""
import csv
import io
import json
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


STATUS_PENDING = "pending"
STATUS_READY = "ready"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

VALID_STATUSES = {STATUS_PENDING, STATUS_READY, STATUS_RUNNING,
                  STATUS_DONE, STATUS_FAILED, STATUS_SKIPPED}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _coerce(value: str):
    """CSV 字符串 → 类型转换（数字 / 布尔 / JSON / 原样字符串）。"""
    v = value.strip()
    if v == "":
        return ""
    if v in ("true", "TRUE", "True"):
        return True
    if v in ("false", "FALSE", "False"):
        return False
    if v in ("null", "None"):
        return None
    # 数字
    if re.fullmatch(r"-?\d+", v):
        try:
            return int(v)
        except ValueError:
            pass
    if re.fullmatch(r"-?\d+\.\d+", v):
        try:
            return float(v)
        except ValueError:
            pass
    # JSON 字面量（数组 / 对象）
    if v.startswith(("[", "{")):
        try:
            return json.loads(v)
        except ValueError:
            pass
    return value


@dataclass
class ProducerTask:
    """单个生产任务。"""
    id: str
    template: str                 # 模板名（库内相对名）或完整路径
    params: dict = field(default_factory=dict)
    output: str = ""              # 输出路径（相对 output_dir 或绝对）
    name: str = ""
    status: str = STATUS_PENDING
    error: str = ""
    attempts: int = 0
    created_at: str = ""
    updated_at: str = ""
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class TaskManager:
    """任务列表管理器（JSON 持久化）。

    Args:
        tasks_file: 任务文件路径（JSON，列表）
        template_dir: 模板库目录（解析相对模板名；可为 None 表示路径直给）
        output_dir: 输出目录（解析相对输出路径；可为 None）
    """

    def __init__(self, tasks_file: str,
                 template_dir: Optional[str] = None,
                 output_dir: Optional[str] = None):
        self.tasks_file = Path(tasks_file)
        self.template_dir = Path(template_dir) if template_dir else None
        self.output_dir = Path(output_dir) if output_dir else None
        self.tasks: list[ProducerTask] = []
        self._load()

    # ---------- 持久化 ----------

    def _load(self) -> None:
        if not self.tasks_file.exists():
            self.tasks = []
            return
        try:
            data = json.loads(self.tasks_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = []
        self.tasks = [ProducerTask(**t) for t in data] if isinstance(data, list) else []

    def save(self) -> None:
        self.tasks_file.parent.mkdir(parents=True, exist_ok=True)
        self.tasks_file.write_text(
            json.dumps([t.to_dict() for t in self.tasks],
                       ensure_ascii=False, indent=2),
            encoding="utf-8")

    # ---------- 路径解析 ----------

    def resolve_template(self, template: str) -> Optional[Path]:
        """解析模板名 → 路径。

        规则：已是存在的路径 → 直接用；否则在 template_dir 下找
        <name>.motion（name 可带或不带 .motion 后缀）。
        """
        p = Path(template)
        if p.exists():
            return p
        if not self.template_dir:
            return None
        candidates = [self.template_dir / template,
                      self.template_dir / f"{template}.motion"]
        for c in candidates:
            if c.exists():
                return c
        return None

    def resolve_output(self, task: ProducerTask) -> str:
        """解析任务输出路径（绝对路径优先，否则拼 output_dir）。"""
        if not task.output:
            return ""
        out = Path(task.output)
        if out.is_absolute() or not self.output_dir:
            return str(out)
        return str(self.output_dir / out)

    # ---------- 增删改查 ----------

    def add(self, template: str, params: Optional[dict] = None,
            output: str = "", name: str = "",
            validate: bool = True) -> ProducerTask:
        """新增任务（自动分配 id，导入即预校验）。"""
        task = ProducerTask(
            id=self._next_id(template),
            template=template,
            params=params or {},
            output=output,
            name=name or Path(template).stem,
            status=STATUS_PENDING,
            created_at=_now(),
            updated_at=_now(),
        )
        self.tasks.append(task)
        if validate:
            self._validate_task(task)
            task.updated_at = _now()
        self.save()
        return task

    def import_csv(self, path: str) -> list[ProducerTask]:
        """从 CSV 导入任务列表。

        列约定：
          template   模板名（必填，可带 .motion 后缀）
          name       任务名（可选）
          output     输出文件名/路径（可选；缺省用 <模板名>.mp4）
          param.<k>  模板参数列（展开为 params[k]，自动类型转换）
          其他列      写入 meta
        """
        text = Path(path).read_text(encoding="utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        if "template" not in (reader.fieldnames or []):
            raise ValueError("CSV 缺少 template 列（首列应为模板名）")
        imported = []
        for row in reader:
            if not row.get("template"):
                continue
            params, meta = {}, {}
            for key, value in row.items():
                if key in (None, "") or value is None:
                    continue
                if key.startswith("param."):
                    params[key[len("param."):]] = _coerce(value)
                elif key in ("template", "name", "output"):
                    continue
                else:
                    meta[key] = _coerce(value)
            task = self.add(
                template=row["template"],
                params=params,
                output=row.get("output", ""),
                name=row.get("name", ""),
            )
            if meta:
                task.meta.update(meta)
                task.updated_at = _now()
            imported.append(task)
        self.save()
        return imported

    def import_json(self, path: str) -> list[ProducerTask]:
        """从 JSON 任务数组导入：[{template, params, output, name}, ...]。"""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("JSON 任务文件必须是数组（[{template, params, ...}]）")
        imported = []
        for item in data:
            if not isinstance(item, dict) or not item.get("template"):
                continue
            task = self.add(
                template=item["template"],
                params=item.get("params") or {},
                output=item.get("output", ""),
                name=item.get("name", ""),
            )
            imported.append(task)
        return imported

    def update(self, task_id: str, **fields) -> Optional[ProducerTask]:
        """更新任务字段（params/output/name/status/error 等）。"""
        task = self.get(task_id)
        if task is None:
            return None
        for key, value in fields.items():
            if not hasattr(task, key):
                continue
            setattr(task, key, value)
        task.updated_at = _now()
        self.save()
        return task

    def remove(self, task_id: str) -> bool:
        before = len(self.tasks)
        self.tasks = [t for t in self.tasks if t.id != task_id]
        removed = len(self.tasks) < before
        if removed:
            self.save()
        return removed

    def get(self, task_id: str) -> Optional[ProducerTask]:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def list_tasks(self, status: Optional[str] = None) -> list[ProducerTask]:
        if status:
            return [t for t in self.tasks if t.status == status]
        return list(self.tasks)

    def clear(self) -> None:
        self.tasks = []
        self.save()

    # ---------- 预校验 ----------

    def _validate_task(self, task: ProducerTask) -> None:
        """模板存在性 + 参数 schema 预校验（standard 可提取 schema）。"""
        path = self.resolve_template(task.template)
        if path is None:
            task.status = STATUS_SKIPPED
            task.error = f"模板不存在: {task.template}"
            return
        task.meta["template_path"] = str(path)
        task.meta["template_format"] = self._detect_format(path)

        # 参数 schema 预校验（standard 明文；personal 加密无法提取 → 跳过）
        schema = self._extract_schema(path)
        if schema:
            errors = self._validate_params(path, schema, task.params)
            if errors:
                task.status = STATUS_SKIPPED
                task.error = "参数未通过 schema 预校验: " + "; ".join(errors)
                return
        task.status = STATUS_READY

    @staticmethod
    def _detect_format(path: Path) -> str:
        try:
            import zipfile
            with zipfile.ZipFile(path, "r") as z:
                if "personal/motiongit.key.json" in z.namelist():
                    return "personal-download-v1"
            return "standard"
        except Exception:
            return "unknown"

    @staticmethod
    def _extract_schema(path: Path) -> Optional[dict]:
        """从模板 ZIP 提取 params.schema.json（无则 None）。"""
        try:
            import zipfile
            with zipfile.ZipFile(path, "r") as z:
                for candidate in ["params.schema.json",
                                  "template/params.schema.json",
                                  "core/params.schema.json"]:
                    if candidate in z.namelist():
                        return json.loads(z.read(candidate).decode("utf-8"))
        except Exception:
            return None
        return None

    @staticmethod
    def _validate_params(path: Path, schema: dict,
                         params: dict) -> list[str]:
        from ..param_manager.schema import ParamSchema
        return ParamSchema(schema).validate(params)

    # ---------- 汇总 ----------

    def status_summary(self) -> dict:
        counts = {s: 0 for s in VALID_STATUSES}
        for t in self.tasks:
            counts[t.status] = counts.get(t.status, 0) + 1
        return {
            "total": len(self.tasks),
            "pending": counts[STATUS_PENDING],
            "ready": counts[STATUS_READY],
            "running": counts[STATUS_RUNNING],
            "done": counts[STATUS_DONE],
            "failed": counts[STATUS_FAILED],
            "skipped": counts[STATUS_SKIPPED],
        }

    def to_dict(self) -> dict:
        return {
            "summary": self.status_summary(),
            "tasks": [t.to_dict() for t in self.tasks],
        }

    # ---------- 内部 ----------

    def _next_id(self, template: str) -> str:
        seq = len(self.tasks) + 1
        stem = re.sub(r"[^\w\u4e00-\u9fff-]", "_", Path(template).stem)[:24]
        return f"{stem}-{seq:03d}"
