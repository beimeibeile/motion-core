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
# Template-shipped fragment renderer (_render/render.mjs): --template/--data/--out
MJS_FRAGMENT = "mjs-fragment"

FRAGMENT_RENDERER = "_render/render.mjs"


def _release_listing(template_path: str) -> Optional[list[str]]:
    """Return the nested release.bin entry list, or None when unreadable."""
    path = Path(template_path)
    if not path.is_file():
        return None
    try:
        with zipfile.ZipFile(path) as outer:
            rel = next((n for n in outer.namelist() if n.endswith("release.bin")), None)
            if rel is None:
                return None
            payload = outer.read(rel)
            if payload[:4] != b"PK\x03\x04":
                return None
            with zipfile.ZipFile(__import__("io").BytesIO(payload)) as inner:
                return inner.namelist()
    except Exception:
        return None


def has_embedded_adapter(template_path: str) -> bool:
    """True if the nested release carries its own runnable renderer.

    Two shapes are supported: `src/adapter.{py,mjs}` (adapter contracts) and
    `_render/render.mjs` (template-shipped fragment renderer, the runtime
    extension the platform's default html-canvas chain does not replace).
    """
    names = _release_listing(template_path)
    if names is None:
        return False
    return any(n.startswith("src/adapter.") for n in names) or FRAGMENT_RENDERER in names


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

    fragment = bundle_dir / FRAGMENT_RENDERER
    if fragment.is_file():
        source = fragment.read_text(encoding="utf-8", errors="replace")
        if "--template" in source and "--data" in source and "--out" in source:
            return MJS_FRAGMENT, fragment
        raise RenderError("env", "_render/render.mjs 未声明 --template/--data/--out，不在已知契约内")

    raise RenderError("env", "嵌套内容中未找到 src/adapter.py、src/adapter.mjs 或 _render/render.mjs")


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
        elif self.contract == MJS_FRAGMENT:
            produced = self._run_mjs_fragment(params_file, progress_cb)
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

    def _run_mjs_fragment(self, params_file: Path,
                          progress_cb: Callable) -> Path:
        """Drive the template-shipped fragment renderer (_render/render.mjs).

        This renderer is the template runtime extension (transparent/chroma base,
        CDP frame capture, anchor-aligned SFX). It is independent of the platform
        helper's default canvas chain, so it is used as-is instead of being
        downgraded to that chain's argument shape.
        """
        out_dir = self.instance_dir / "fragment_out"
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True)
        # render.mjs treats --out as a filename BASE and appends suffixes
        # (_green_key.mp4 / _transparent_prores4444.mov / _transparent_vp9.webm).
        out_base = out_dir / "video"
        entry = "src/index.html"
        codec = os.environ.get("O_MOTION_FRAGMENT_CODEC", "h264")
        bg = os.environ.get("O_MOTION_FRAGMENT_BG", "transparent")
        # 模板自带的锚点音效表（assets/sfx/*.json）：有则照用，不替作者删音效
        sfx = None
        sfx_dir = self.bundle_dir / "assets" / "sfx"
        if sfx_dir.is_dir():
            candidates = sorted(sfx_dir.glob("*.json"))
            sfx = candidates[0] if candidates else None
        if progress_cb:
            progress_cb("render", 60, "模板内置渲染器出片...")
        argv = ["node", str(self.adapter),
                "--template", str(self.bundle_dir / entry),
                "--data", str(params_file),
                "--out", str(out_base),
                "--bg", bg,
                "--codec", codec]
        if sfx is not None:
            argv += ["--sfx", str(sfx)]
        self._checked("render", argv, self.bundle_dir, timeout=1800)
        for pattern in ("*.mp4", "*.mov", "*.webm"):
            found = sorted(out_dir.rglob(pattern))
            if found:
                return found[0]
        raise RenderError("render", "模板内置渲染器未产出成品", str(out_dir))

    def _checked(self, stage: str, argv: list, cwd: Path,
                 timeout: int = 600) -> None:
        code, out, err = self._run(argv, cwd, timeout=timeout)
        if code != 0:
            raise RenderError(stage, f"embedded adapter {stage} 失败",
                              (err + out)[-600:])
