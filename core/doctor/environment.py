"""Environment doctor - 一键收集 node/ffmpeg/Chrome/模板完整性。

对应 OpenFilm 借鉴 P3「doctor 式环境自检命令」（低成本高收益）：
渲染链路依赖探测 + 模板完整性检查，一次性给出可行动结论。
"""

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

from ..template_parser.strict import lint_template, strict_json_load

_TIMEOUT = 20

_CHROME_CANDIDATES = [
    # Windows Chrome
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    # Playwright 缓存（chromium-1169 等）
    r"%LOCALAPPDATA%\ms-playwright",
]


def _run(cmd: List[str]) -> Optional[str]:
    """运行命令取首行输出；失败/超时返回 None。"""
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=_TIMEOUT)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    out = (proc.stdout or proc.stderr or b"").decode("utf-8", errors="replace")
    return out.strip().splitlines()[0] if out.strip() else "ok"


@dataclass
class DoctorItem:
    """单项检查结果。status: ok / warn / missing。"""
    name: str
    status: str
    detail: str
    command: str = ""


@dataclass
class DoctorReport:
    """doctor 报告：环境项 + 模板完整性。healthy = 无 missing。"""
    items: List[DoctorItem] = field(default_factory=list)
    template_checks: List[DoctorItem] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not any(i.status == "missing" for i in self.items)

    @property
    def summary(self) -> dict:
        def count(items):
            return {
                "ok": sum(1 for i in items if i.status == "ok"),
                "warn": sum(1 for i in items if i.status == "warn"),
                "missing": sum(1 for i in items if i.status == "missing"),
            }
        return {"environment": count(self.items),
                "template": count(self.template_checks)}

    def to_dict(self) -> dict:
        return {
            "healthy": self.healthy,
            "summary": self.summary,
            "environment": [
                {"name": i.name, "status": i.status, "detail": i.detail,
                 "command": i.command} for i in self.items
            ],
            "template": [
                {"name": i.name, "status": i.status, "detail": i.detail,
                 "command": i.command} for i in self.template_checks
            ],
        }

    def to_markdown(self) -> str:
        lines = [
            "## 环境自检报告（doctor）",
            f"- 健康：{'✅ 可用' if self.healthy else '❌ 存在缺失'}",
            "",
            "### 渲染链路环境",
            "",
            "| 项 | 状态 | 详情 |",
            "|---|---|---|",
        ]
        for i in self.items:
            mark = {"ok": "✅", "warn": "⚠️", "missing": "❌"}[i.status]
            lines.append(f"| {i.name} | {mark} {i.status} | {i.detail} |")
        if self.template_checks:
            lines += ["", "### 模板完整性", "",
                      "| 项 | 状态 | 详情 |", "|---|---|---|"]
            for i in self.template_checks:
                mark = {"ok": "✅", "warn": "⚠️", "missing": "❌"}[i.status]
                lines.append(f"| {i.name} | {mark} {i.status} | {i.detail} |")
        return "\n".join(lines)


def check_node() -> DoctorItem:
    out = _run(["node", "--version"])
    if out is None:
        return DoctorItem("node", "missing", "未找到 node 或执行失败",
                          command="node --version")
    return DoctorItem("node", "ok", out, command="node --version")


def check_ffmpeg() -> DoctorItem:
    out = _run(["ffmpeg", "-version"])
    if out is None:
        return DoctorItem("ffmpeg", "missing", "未找到 ffmpeg（渲染/抽帧/波形依赖）",
                          command="ffmpeg -version")
    return DoctorItem("ffmpeg", "ok", out, command="ffmpeg -version")


def check_ffprobe() -> DoctorItem:
    out = _run(["ffprobe", "-version"])
    if out is None:
        return DoctorItem("ffprobe", "warn", "未找到 ffprobe（元数据探测降级）",
                          command="ffprobe -version")
    return DoctorItem("ffprobe", "ok", out, command="ffprobe -version")


def check_chrome() -> DoctorItem:
    found = None
    for pattern in _CHROME_CANDIDATES:
        p = os.path.expandvars(pattern)
        if os.path.isfile(p):
            found = p
            break
    if found:
        return DoctorItem("chrome", "ok", found)
    # Playwright 缓存目录存在也算可用（内含浏览器可执行文件）
    cache = os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright")
    if os.path.isdir(cache) and os.listdir(cache):
        return DoctorItem("chrome", "warn",
                          f"未在常见路径找到 chrome.exe；"
                          f"playwright 缓存存在：{cache}")
    return DoctorItem("chrome", "missing",
                      "未找到 Chrome（无头渲染依赖），常见路径均已探测",
                      command="chrome.exe 常见路径探测")


def check_template(template_dir: str) -> List[DoctorItem]:
    """模板完整性：lint 报告映射为 doctor 条目。"""
    checks: List[DoctorItem] = []
    if not os.path.isdir(template_dir):
        return [DoctorItem("template", "missing",
                           f"模板目录不存在: {template_dir}")]
    report = lint_template(template_dir)
    errors = [f for f in report.findings if f.severity == "error"]
    warns = [f for f in report.findings if f.severity == "warn"]
    checks.append(DoctorItem(
        "lint", "ok" if report.healthy else "warn" if errors else "ok",
        f"结构 lint：error {report.summary['errors']} / "
        f"warn {report.summary['warnings']}"
        + (f"；首个 error：{errors[0].message}" if errors else ""),
        command="lint_template"))
    for f in warns[:3]:
        checks.append(DoctorItem("template", "warn", f.message, f.file or ""))
    return checks


def run_doctor(template_dir: Optional[str] = None) -> DoctorReport:
    """一键自检：node/ffmpeg/ffprobe/Chrome + 可选模板完整性。"""
    report = DoctorReport()
    report.items = [check_node(), check_ffmpeg(), check_ffprobe(), check_chrome()]
    if template_dir:
        report.template_checks = check_template(template_dir)
    return report
