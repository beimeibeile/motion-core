"""Embedded-adapter render engine.

Some personal-download-v1 packages ship an executable adapter inside the nested
``content/release.bin`` (``src/adapter.py`` or ``src/adapter.mjs``), while the
outer helper (``assistant/motion.mjs``) always dispatches a fixed
``check-params -> plan -> prepare -> preview -> render`` chain. When the embedded
adapter implements only a subset of those subcommands (or only accepts
``/``-relative paths), the helper fails at the first step even though the adapter
itself is fully runnable.

This engine calls the package's own adapter directly. The command shape is not
guessed from template ids: it is detected from the adapter's real help surface
(or its source for the node entry), then mapped to one of three generic contract
profiles.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Optional

from .engine import RenderEngine, RenderError

# Contract profile names
PY_POSITIONAL = "py-positional"
PY_OP_FLAGS = "py-op-flags"
MJS_INSTANCE = "mjs-instance"


def has_embedded_adapter(template_path: str) -> bool:
    """True if the package's nested release contains src/adapter.{py,mjs}."""
    path = Path(template_path)
    if not path.is_file():
        return False
    try:
        with zipfile.ZipFile(path) as outer:
            rel = next((n for n in outer.namelist() if n.endswith("release.bin")), None)
            if rel is None:
                return False
            payload = outer.read(rel)
            if payload[:4] != b"PK\x03\x04":
                return False
            with zipfile.ZipFile(__import__("io").BytesIO(payload)) as inner:
                return any(n.startswith("src/adapter.") for n in inner.namelist())
    except Exception:
        return False


def find_playwright_module(explicit: Optional[str] = None) -> Optional[str]:
    """Locate a playwright module without hardcoded machine paths.

    Order: explicit arg -> PLAYWRIGHT_MODULE env -> helper dependency caches
    (playwright-core) under the system temp dir.
    """
    if explicit:
        return str(Path(explicit).expanduser().resolve())
    env = os.environ.get("PLAYWRIGHT_MODULE")
    if env:
        return env
    tmp = Path(tempfile.gettempdir())
    for cand in sorted(tmp.glob("motiongit-dependencies-*/node_modules/playwright-core")):
        return str(cand)
    return None


def detect_contract(bundle_dir: Path) -> tuple[str, Path]:
    """Detect the adapter contract from its real entry surface.

    Returns (profile, adapter_path). Raises RenderError if no usable adapter.
    """
    py_adapter = bundle_dir / "src" / "adapter.py"
    mjs_adapter = bundle_dir / "src" / "adapter.mjs"

    if py_adapter.is_file():
        code, out, err = _probe_help([sys.executable, str(py_adapter), "-h"], bundle_dir)
        surface = f"{out}\n{err}"
        if code != 0 and "usage:" not in surface:
            raise RenderError("env", "无法读取 adapter.py 的命令面", (err + out)[-400:])
        # argparse prints the global option surface; --inputs/--out means the
        # flag-based two-stage contract, otherwise positional plan/render.
        if "--inputs" in surface and "--out" in surface:
            return PY_OP_FLAGS, py_adapter
        return PY_POSITIONAL, py_adapter

    if mjs_adapter.is_file():
        # The node entry has no -h contract; verify its declared --instance
        # surface from source instead of guessing.
        source = mjs_adapter.read_text(encoding="utf-8", errors="replace")
        if "--instance" not in source:
            raise RenderError("env", "adapter.mjs 未声明 --instance，不在已知契约内")
        return MJS_INSTANCE, mjs_adapter

    raise RenderError("env", "嵌套内容中未找到 src/adapter.py 或 src/adapter.mjs")


def _probe_help(argv: list, cwd: Path) -> tuple[int, str, str]:
    """Run a help probe without the engine shell wrapper."""
    proc = __import__("subprocess").run(
        argv, cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


class EmbeddedAdapterEngine(RenderEngine):
    """Render engine for personal packages driven by an embedded adapter."""

    def __init__(self, template_path: str, instance_dir: str):
        super().__init__(template_path, instance_dir)
        self.contract: Optional[str] = None
        self.adapter: Optional[Path] = None
        self.bundle_dir: Optional[Path] = None

    # ── prepare ────────────────────────────────────────────────────────
    def prepare(self) -> None:
        outer_dir = self.instance_dir / "template"
        if outer_dir.exists():
            shutil.rmtree(outer_dir)
        outer_dir.mkdir(parents=True)
        with zipfile.ZipFile(self.template_path) as z:
            z.extractall(outer_dir)

        rel = next((n for n in outer_dir.rglob("release.bin")), None)
        if rel is None:
            raise RenderError("env", "缺少 content/release.bin")
        bundle_dir = self.instance_dir / "bundle"
        if bundle_dir.exists():
            shutil.rmtree(bundle_dir)
        bundle_dir.mkdir(parents=True)
        with zipfile.ZipFile(rel) as inner:
            inner.extractall(bundle_dir)
        self.bundle_dir = bundle_dir
        self.contract, self.adapter = detect_contract(bundle_dir)

    # ── render ────────────────────────────────────────────────────────
    def render(self, params: dict, output_path: str,
               progress_cb: Callable = None) -> dict:
        if not self.bundle_dir:
            self.prepare()

        t0 = __import__("time").time()
        out_path = Path(output_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        params_file = self.instance_dir / "params.json"
        params_file.write_text(json.dumps(params, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        pw = find_playwright_module()

        if self.contract in (PY_POSITIONAL, PY_OP_FLAGS):
            if not pw:
                raise RenderError(
                    "env",
                    "未找到 playwright 模块；用环境变量 PLAYWRIGHT_MODULE 或 --playwright-module 指定",
                )

        if self.contract == PY_POSITIONAL:
            produced = self._run_py_positional(params_file, pw, progress_cb)
        elif self.contract == PY_OP_FLAGS:
            produced = self._run_py_op_flags(params_file, pw, progress_cb)
        else:
            produced = self._run_mjs_instance(params_file, progress_cb)

        if not produced.is_file():
            raise RenderError("render", "adapter 未产出成品", str(produced))
        if out_path.exists():
            out_path.unlink()
        shutil.copy2(produced, out_path)

        elapsed = __import__("time").time() - t0
        if progress_cb:
            progress_cb("done", 100, f"完成 ({elapsed:.1f}s)")
        return {
            "success": True,
            "output_path": str(out_path),
            "elapsed_s": round(elapsed, 1),
            "format": "personal-download-v1",
            "contract": self.contract,
        }

    # ── contract profiles ─────────────────────────────────────────────
    def _run_py_positional(self, params_file: Path, pw: str,
                           progress_cb: Callable) -> Path:
        work = self.instance_dir / "embed"
        if work.exists():
            shutil.rmtree(work)  # adapter refuses an existing instance
        if progress_cb:
            progress_cb("plan", 15, "adapter plan...")
        self._checked(
            "plan",
            [sys.executable, str(self.adapter), "plan", str(params_file), str(work)],
            self.bundle_dir,
        )
        if progress_cb:
            progress_cb("render", 60, "adapter render...")
        self._checked(
            "render",
            [sys.executable, str(self.adapter), "render", str(work),
             "--playwright-module", pw],
            self.bundle_dir, timeout=1800,
        )
        return work / "video.mp4"

    def _run_py_op_flags(self, params_file: Path, pw: str,
                         progress_cb: Callable) -> Path:
        plan_out = self.instance_dir / "plan_out"
        render_out = self.instance_dir / "render_out"
        for d in (plan_out, render_out):
            if d.exists():
                shutil.rmtree(d)
        if progress_cb:
            progress_cb("plan", 15, "adapter plan...")
        self._checked(
            "plan",
            [sys.executable, str(self.adapter), "plan",
             "--inputs", str(params_file), "--out", str(plan_out)],
            self.bundle_dir,
        )
        if progress_cb:
            progress_cb("render", 60, "adapter render...")
        self._checked(
            "render",
            [sys.executable, str(self.adapter), "render",
             "--inputs", str(params_file), "--out", str(render_out),
             "--playwright-module", pw],
            self.bundle_dir, timeout=1800,
        )
        return render_out / "video.mp4"

    def _run_mjs_instance(self, params_file: Path,
                          progress_cb: Callable) -> Path:
        embed = self.instance_dir / "embed"
        if embed.exists():
            shutil.rmtree(embed)
        embed.mkdir(parents=True)
        shutil.copy2(params_file, embed / "inputs.json")
        stages = [
            ("plan", 15, 300),
            ("prepare", 40, 600),
            ("preview", 55, 300),
            ("render", 70, 1800),
        ]
        for stage, pct, timeout in stages:
            if progress_cb:
                progress_cb(stage, pct, f"adapter {stage}...")
            self._checked(
                stage,
                ["node", str(self.adapter), stage, "--instance", str(embed)],
                self.bundle_dir, timeout=timeout,
            )
        return embed / "video.mp4"

    def _checked(self, stage: str, argv: list, cwd: Path,
                 timeout: int = 600) -> None:
        code, out, err = self._run(argv, cwd, timeout=timeout)
        if code != 0:
            raise RenderError(stage, f"embedded adapter {stage} 失败",
                              (err + out)[-600:])
