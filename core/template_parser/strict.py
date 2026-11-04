"""Strict template parsing with precise line:col errors + structure lint.

对应 OpenFilm 借鉴 P3「严格解析 + 行号报错」：模板结构 lint 与 .motion
官方校验互补——官方校验管"契约正确性"，本模块管"可读性与结构完整性"，
解析失败时报文件:行:列，便于模板作者直接定位。
"""

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

# 必需文件（所有 .motion 模板包都应具备）
REQUIRED_FILES = ["motion.json", "params.schema.json", "guide.json"]

# motion.json 必填字段（按 standard / seed-core 实测校准）
REQUIRED_MOTION_FIELDS = ["id", "name", "version", "runtime"]

# 可选契约文件（存在时会被严格解析）
OPTIONAL_CONTRACTS = [
    "copy-contract.json", "integrity.json", "source-inventory.json",
    "media-clock-contract.json", "audio-content-boundary.json",
    "brand-content-boundary.json", "production-music.json",
]

_SKIP_DIRS = {"node_modules", ".git", "__pycache__", "assets", "licenses"}


def strict_json_load(path: str) -> Any:
    """严格解析 JSON：失败时报 文件:行:列 + 原因。

    容忍 UTF-8 BOM 与 \r\n；解析失败抛 ValueError，定位精确到行列。
    """
    if not os.path.isfile(path):
        raise ValueError(f"{path}: 文件不存在")
    raw = open(path, "rb").read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"{path}: 非 UTF-8 文本（byte {e.start}）") from e
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        line = text.count("\n", 0, e.pos) + 1
        col = e.pos - (text.rfind("\n", 0, e.pos) + 1) + 1
        raise ValueError(f"{path}:{line}:{col}: JSON 解析失败: {e.msg}") from e


@dataclass
class LintFinding:
    """lint 单条发现。severity: error（不健康）/ warn（提示）。"""
    severity: str
    message: str
    file: Optional[str] = None


@dataclass
class LintReport:
    """模板结构 lint 报告。healthy = 无 error。"""
    template_dir: str
    findings: List[LintFinding] = field(default_factory=list)

    @property
    def summary(self) -> dict:
        return {
            "errors": sum(1 for f in self.findings if f.severity == "error"),
            "warnings": sum(1 for f in self.findings if f.severity == "warn"),
        }

    @property
    def healthy(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    def to_dict(self) -> dict:
        return {
            "template_dir": self.template_dir,
            "healthy": self.healthy,
            "summary": self.summary,
            "findings": [
                {"severity": f.severity, "message": f.message, "file": f.file}
                for f in self.findings
            ],
        }

    def to_markdown(self) -> str:
        lines = [
            f"## 模板结构 lint 报告",
            f"- 模板目录：`{self.template_dir}`",
            f"- 健康：{'✅ 通过' if self.healthy else '❌ 存在错误'} "
            f"（error {self.summary['errors']} / warn {self.summary['warnings']}）",
            "",
        ]
        if not self.findings:
            lines.append("_未发现结构问题。_")
            return "\n".join(lines)
        lines.append("| 级别 | 文件 | 发现 |")
        lines.append("|---|---|---|")
        for f in self.findings:
            lines.append(f"| {f.severity} | {f.file or '-'} | {f.message} |")
        return "\n".join(lines)


def _is_required_json(path: str) -> bool:
    return path.replace("\\", "/").endswith(
        ("/motion.json", "/params.schema.json", "/guide.json"))


def lint_template(template_dir: str) -> LintReport:
    """模板结构 lint：必需文件 + 严格解析 + 必填字段 + 可选契约。

    只读审计，不修改任何文件；目录不存在抛 ValueError。
    """
    if not os.path.isdir(template_dir):
        raise ValueError(f"模板目录不存在: {template_dir}")
    report = LintReport(template_dir)

    # 1) 必需文件存在性
    missing = [f for f in REQUIRED_FILES
               if not os.path.isfile(os.path.join(template_dir, f))]
    for f in missing:
        report.findings.append(
            LintFinding("error", f"必需文件缺失: {f}", f))

    # 2) 必需 JSON 严格解析
    for f in REQUIRED_FILES:
        p = os.path.join(template_dir, f)
        if not os.path.isfile(p):
            continue
        try:
            strict_json_load(p)
        except ValueError as e:
            report.findings.append(LintFinding("error", str(e), f))

    # 3) motion.json 必填字段。两种形态实测：standard 与 seed-core 的
    #    id/name/version 位于 template 子对象、runtime 位于顶层——
    #    合并两来源后统一校验。
    motion_path = os.path.join(template_dir, "motion.json")
    if os.path.isfile(motion_path):
        try:
            motion = strict_json_load(motion_path)
            meta = {}
            if isinstance(motion.get("template"), dict):
                meta.update(motion["template"])
            for key in REQUIRED_MOTION_FIELDS:
                if motion.get(key):
                    meta[key] = motion[key]
            for key in REQUIRED_MOTION_FIELDS:
                if not meta.get(key):
                    report.findings.append(
                        LintFinding("error", f"motion.json 缺少必填字段: {key}",
                                    "motion.json"))
        except ValueError:
            pass  # 已由第 2 步报错

    # 4) 可选契约文件严格解析（存在即可读）
    for f in OPTIONAL_CONTRACTS:
        p = os.path.join(template_dir, f)
        if os.path.isfile(p):
            try:
                strict_json_load(p)
            except ValueError as e:
                report.findings.append(LintFinding("error", str(e), f))

    # 5) 源码目录存在性提示（native / adapter）
    for sub in ("native", "adapter"):
        if not os.path.isdir(os.path.join(template_dir, sub)):
            report.findings.append(
                LintFinding("warn", f"未发现 {sub}/ 源码目录"
                            "（部分模板为纯参数模板可忽略）", sub))

    return report
