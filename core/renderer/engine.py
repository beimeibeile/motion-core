"""Render engine - wraps template-specific render backends.

Supports:
- standard format: adapter.py (uv run) -> render.mjs -> mix.py
- personal-download-v1 (v1 runtime): motion.mjs run --workflow motion-kit
- personal-download-v1 (v2 runtime): split_root.py / canvas_split_root.py
  (auto-routed from assistant/BUILD.json profiles; E7-verified 2026-10-28)
"""

import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Callable, Optional

_SHELL = os.name == 'nt'

# v2 runtime marker in assistant/BUILD.json profiles
V2_PROFILE_MARKER = 'split-root-v1'


class RenderError(Exception):
    """Render error with stage context."""
    def __init__(self, stage: str, message: str, detail: str = None):
        self.stage = stage
        self.message = message
        self.detail = detail
        super().__init__(f"[{stage}] {message}")


class RenderEngine:
    """Base render engine with common utilities."""

    def __init__(self, template_path: str, instance_dir: str):
        self.template_path = Path(template_path).resolve()
        self.instance_dir = Path(instance_dir).resolve()
        self.extracted_dir: Optional[Path] = None

    def _run(self, cmd: list, cwd: Path, timeout: int = 600,
             env: dict = None) -> tuple[int, str, str]:
        """Run a command and return (returncode, stdout, stderr).

        Uses UTF-8 decoding with error replacement to handle Windows GBK issues.
        Default env includes UTF-8 flags + Chrome + ffmpeg PATH probing.
        """
        result = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True,
            timeout=timeout, shell=_SHELL,
            env=env if env is not None else self._build_env()
        )
        stdout = result.stdout.decode('utf-8', errors='replace') if result.stdout else ''
        stderr = result.stderr.decode('utf-8', errors='replace') if result.stderr else ''
        return result.returncode, stdout, stderr

    @staticmethod
    def _build_env() -> dict:
        """Build runtime environment: UTF-8 + Chrome + ffmpeg PATH probing.

        ffmpeg 探测顺序（仓库不存本机绝对路径）：
          1. FFMPEG_BIN 环境变量（显式配置优先）
          2. 当前 PATH 中已有 ffmpeg（shutil.which）
          3. Windows 用户级 PATH（HKCU\\Environment，用户持久化后即时生效）
          4. 常见安装根：ProgramFiles / ProgramFiles(x86) / LOCALAPPDATA /
             USERPROFILE 下的 ffmpeg/bin（含 ffmpeg-master-latest-win64-gpl 变体）
        """
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["LANG"] = "en_US.UTF-8"
        # Find system Chrome（运行时探测，仓库不存本机绝对路径）
        chrome_paths = [
            os.path.join(os.environ.get("ProgramFiles", ""),
                         r"Google\Chrome\Application\chrome.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", ""),
                         r"Google\Chrome\Application\chrome.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""),
                         r"Google\Chrome\Application\chrome.exe"),
        ]
        for p in chrome_paths:
            if Path(p).exists():
                env["CHROME_PATH"] = p
                break
        # Find ffmpeg and inject its bin dir into PATH
        ffmpeg_bin = os.environ.get("FFMPEG_BIN", "").strip()
        if not ffmpeg_bin:
            import shutil
            in_path = shutil.which("ffmpeg")
            if in_path:
                ffmpeg_bin = os.path.dirname(in_path)
        if not ffmpeg_bin:
            ffmpeg_bin = RenderEngine._ffmpeg_from_user_path()
        if not ffmpeg_bin:
            for root_name in ("ProgramFiles", "ProgramFiles(x86)",
                              "LOCALAPPDATA", "USERPROFILE"):
                root = os.environ.get(root_name, "")
                if not root:
                    continue
                for cand in (os.path.join(root, "ffmpeg", "bin"),
                             os.path.join(root, "ffmpeg-master-latest-win64-gpl",
                                          "bin")):
                    if Path(cand).is_dir():
                        ffmpeg_bin = cand
                        break
                if ffmpeg_bin:
                    break
        if ffmpeg_bin and Path(ffmpeg_bin).is_dir():
            env["FFMPEG_BIN"] = ffmpeg_bin
            env["PATH"] = ffmpeg_bin + os.pathsep + env.get("PATH", "")
        return env

    @staticmethod
    def _ffmpeg_from_user_path() -> str:
        """Read Windows user-level PATH (HKCU\\Environment) for an ffmpeg bin dir.

        Registry read is a runtime probe — the repo stores no machine paths.
        """
        if os.name != 'nt':
            return ""
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                user_path, _ = winreg.QueryValueEx(key, "Path")
        except Exception:
            return ""
        for seg in user_path.split(os.pathsep):
            seg = seg.strip().strip('"')
            if seg and os.path.isfile(os.path.join(seg, "ffmpeg.exe")):
                return seg
        return ""

    def _extract(self) -> Path:
        """Extract .motion template to instance_dir/template."""
        extract_dir = self.instance_dir / "template"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(self.template_path, 'r') as z:
            z.extractall(extract_dir)
        self.extracted_dir = extract_dir
        return extract_dir

    def _ensure_npm(self, directory: Path) -> None:
        """Ensure node_modules installed."""
        if not (directory / "node_modules").exists():
            code, out, err = self._run(['npm', 'ci'], directory, timeout=120)
            if code != 0:
                raise RenderError("env", "npm ci 失败", err[-500:])

    def prepare(self) -> None:
        """Extract template and prepare environment. Override in subclasses."""
        raise NotImplementedError

    def render(self, params: dict, output_path: str,
               progress_cb: Callable = None) -> dict:
        """Render template to output_path. Override in subclasses."""
        raise NotImplementedError


class StandardRenderEngine(RenderEngine):
    """Render engine for standard (plain text) .motion templates.

    Pipeline: uv run adapter.py plan -> prepare -> render.mjs -> mix.py
    """

    def prepare(self) -> None:
        self._extract()
        self._ensure_npm(self.extracted_dir)

    def render(self, params: dict, output_path: str,
               progress_cb: Callable = None) -> dict:
        if not self.extracted_dir:
            self.prepare()

        t0 = time.time()
        out_path = Path(output_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Write params to temp JSON
        params_file = self.instance_dir / "input.json"
        params_file.write_text(json.dumps(params, ensure_ascii=False), encoding='utf-8')

        # 1. plan
        if progress_cb:
            progress_cb("plan", 10, "生成制作计划...")
        code, out, err = self._run(
            ['uv', 'run', 'adapter.py', 'plan', '--input', str(params_file)],
            self.extracted_dir, timeout=120
        )
        if code != 0:
            raise RenderError("plan", "adapter.py plan 失败", (err + out)[-500:])

        # 2. prepare
        if progress_cb:
            progress_cb("prepare", 30, "构建渲染项目...")
        project_dir = self.instance_dir / "project"
        # adapter.py refuses to overwrite existing project dir
        if project_dir.exists():
            shutil.rmtree(project_dir)
        code, out, err = self._run(
            ['uv', 'run', 'adapter.py', 'prepare', '--input', str(params_file),
             '--out', str(project_dir)],
            self.extracted_dir, timeout=180
        )
        if code != 0:
            raise RenderError("prepare", "adapter.py prepare 失败", (err + out)[-500:])

        # 3. render (render.mjs -> mix.py handled by adapter.py render)
        if progress_cb:
            progress_cb("render", 60, "渲染视频...")
        # adapter.py/mix.py refuse to overwrite existing outputs
        if out_path.exists():
            out_path.unlink()
        # mix.py produces <stem>-with-audio.mp4 as final output
        with_audio = out_path.with_name(out_path.stem + "-with-audio.mp4")
        if with_audio.exists():
            with_audio.unlink()
        # mix.py refuses to overwrite existing audio output in project dir
        audio_out = project_dir / "audio-output"
        if audio_out.exists():
            shutil.rmtree(audio_out)
        code, out, err = self._run(
            ['uv', 'run', 'adapter.py', 'render', '--instance', str(project_dir),
             '--out', str(out_path)],
            self.extracted_dir, timeout=600
        )
        if code != 0:
            raise RenderError("render", "adapter.py render 失败", (err + out)[-500:])

        elapsed = time.time() - t0
        if progress_cb:
            progress_cb("done", 100, f"完成 ({elapsed:.1f}s)")

        return {
            'success': True,
            'output_path': str(out_path),
            'elapsed_s': round(elapsed, 1),
            'format': 'standard',
            'project_dir': str(project_dir),
        }


class PersonalRenderEngine(RenderEngine):
    """Render engine for personal-download-v1 .motion templates.

    Accepts a .motion zip archive OR an unpacked template directory.
    Pipeline (v1 runtime): motion.mjs run --workflow motion-kit
      doctor -> setup -> check-params -> plan -> prepare -> preview -> render
    Pipeline (v2 runtime): motion.mjs run (auto-route, no --workflow)
      setup -> check-params -> plan -> prepare(@vault) -> preview -> render

    Runtime generation is auto-detected from assistant/BUILD.json profiles
    ('split-root-v1' => v2) with fallback to template_parser detection.
    """

    def _extract(self) -> Path:
        """Extract .motion template (zip OR directory) to instance_dir/template."""
        extract_dir = self.instance_dir / "template"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True, exist_ok=True)
        if self.template_path.is_dir():
            shutil.copytree(self.template_path, extract_dir, dirs_exist_ok=True)
        else:
            with zipfile.ZipFile(self.template_path, 'r') as z:
                z.extractall(extract_dir)
        self.extracted_dir = extract_dir
        return extract_dir

    def __init__(self, template_path: str, instance_dir: str):
        super().__init__(template_path, instance_dir)
        self._motion_mjs: Optional[Path] = None
        self._assistant_dir: Optional[Path] = None
        self.runtime_generation: str = 'v1'
        self._v2_kind: Optional[str] = None  # 'split-root' | 'canvas-split-root' | None
        self.core_dir: Optional[Path] = None  # captured decrypted locale core (v2)

    def prepare(self) -> None:
        self._extract()
        self._assistant_dir = self.extracted_dir / "assistant"
        self._motion_mjs = self._assistant_dir / "motion.mjs"
        if not self._motion_mjs.exists():
            raise RenderError("env", "缺少 assistant/motion.mjs")        # CRITICAL: Patch motion.mjs to pass all env vars to child processes
        # (otherwise PYTHONUTF8/CHROME_PATH are lost, causing GBK decode errors)
        self._patch_motion_mjs(self._motion_mjs)
        # Detect v1/v2 runtime generation from assistant/BUILD.json
        self._detect_runtime()
        # Prepare instance dir structure
        self.instance_dir.mkdir(parents=True, exist_ok=True)
        (self.instance_dir / "inputs").mkdir(exist_ok=True)
        # Write motiongit-source.json (required by motion.mjs)
        self._write_source_metadata()

    def _detect_runtime(self) -> None:
        """Detect v1/v2 runtime from assistant/BUILD.json profiles.

        Primary: BUILD.json `profiles` contains 'split-root-v1' => v2.
        Fallback: motion.mjs content contains split_root/canvas_split_root => v2.
        """
        build_file = self._assistant_dir / "BUILD.json"
        if build_file.exists():
            try:
                data = json.loads(build_file.read_text(encoding="utf-8", errors="replace"))
                profiles = data.get("profiles") or []
                if V2_PROFILE_MARKER in profiles:
                    self.runtime_generation = 'v2'
                    # Distinguish canvas (native-frame) vs split-root (narration)
                    # adapter may live in extracted/adapter OR inside locale core
                    self._v2_kind = self._detect_v2_kind()
                    return
            except Exception:
                pass
        # Fallback: probe motion.mjs content
        try:
            content = self._motion_mjs.read_text(encoding="utf-8", errors="replace")[:50000]
            if 'canvas_split_root' in content:
                self.runtime_generation = 'v2'
                self._v2_kind = 'canvas-split-root'
            elif 'split_root' in content:
                self.runtime_generation = 'v2'
                self._v2_kind = 'split-root'
        except Exception:
            pass

    def _capture_v2_core(self) -> Optional[Path]:
        """Copy the decrypted locale core from Temp into instance/core.

        v2 templates keep contracts (params.schema.json, copy-contract.json,
        native-config.json) and adapters inside an encrypted bundle; motion.mjs
        decrypts it to a Temp dir (motiongit-locale-core-*) during a *fresh*
        setup. When dependency cache is warm the core may be absent from Temp;
        callers should clean dependency cache before setup to force capture.
        """
        import tempfile
        temp_dir = Path(tempfile.gettempdir())
        candidates = sorted(
            (d for d in temp_dir.glob('motiongit-locale-core-*') if d.is_dir()),
            key=lambda d: d.stat().st_mtime, reverse=True,
        )
        if not candidates:
            return None
        src = candidates[0]
        core_out = self.instance_dir / 'core'
        if core_out.exists():
            shutil.rmtree(core_out)
        # prefer locale subdir (zh/en) which holds the contracts
        locale_src = None
        for loc in ('zh', 'en'):
            cand = src / loc
            if cand.is_dir():
                locale_src = cand
                break
        shutil.copytree(locale_src or src, core_out)
        self.core_dir = core_out
        # Refine kind from captured core: native-config.json => canvas
        try:
            if (core_out / 'native-config.json').exists():
                cfg = json.loads((core_out / 'native-config.json')
                                 .read_text(encoding='utf-8', errors='replace'))
                if cfg.get('kind') == 'native-frame':
                    self._v2_kind = 'canvas-split-root'
            elif any(core_out.rglob('canvas_split_root.py')):
                self._v2_kind = 'canvas-split-root'
        except Exception:
            pass
        return core_out

    def _detect_v2_kind(self) -> Optional[str]:
        """Distinguish canvas (native-frame) vs split-root (narration).

        Adapter scripts live inside the ENCRYPTED content bundle, so probe
        motion.mjs for the helper names it dispatches to.
        """
        try:
            content = self._motion_mjs.read_text(encoding="utf-8", errors="replace")[:80000]
            if 'canvas_split_root' in content:
                return 'canvas-split-root'
            if 'split_root' in content:
                return 'split-root'
        except Exception:
            pass
        # fallback: any visible adapter script under extracted tree
        for rel in ('adapter/canvas_split_root.py', 'adapter/split_root.py'):
            direct = self.extracted_dir / rel
            if direct.exists():
                return 'canvas-split-root' if 'canvas' in rel else 'split-root'
        for p in self.extracted_dir.rglob('canvas_split_root.py'):
            return 'canvas-split-root'
        for p in self.extracted_dir.rglob('split_root.py'):
            return 'split-root'
        return 'split-root'  # default for split-root-v1 profiles

    @staticmethod
    def _patch_motion_mjs(mjs_path: Path) -> bool:
        """Patch motion.mjs to pass all environment variables to child processes.

        Original: const env = {}; for (const name of [...]) if (process.env[name]) env[name] = ...
        Patched:  const env = { ...process.env };
        This ensures PYTHONUTF8, CHROME_PATH, etc. are inherited by Python subprocesses.
        """
        PATCH_MARKER = "/* omotion-core-patched */"
        if not mjs_path.exists():
            return False
        try:
            content = mjs_path.read_text(encoding="utf-8", errors="replace")
            if PATCH_MARKER in content:
                return True
            import re
            pattern = r'const env = \{\};\s*\n\s*for \(const name of \[[^\]]+\]\)\s*\n\s*if \(process\.env\[name\]\) env\[name\] = process\.env\[name\];'
            replacement = f'const env = {{ ...process.env }}; {PATCH_MARKER}'
            if re.search(pattern, content):
                content = re.sub(pattern, replacement, content)
            elif 'const env = {};' in content:
                content = content.replace('const env = {};', replacement)
            else:
                return False
            mjs_path.write_text(content, encoding="utf-8")
            return True
        except Exception:
            return False

    def _write_source_metadata(self) -> None:
        """Write motiongit-source.json using `motion.mjs inspect` output."""
        import json as _json
        source_file = self.instance_dir / "motiongit-source.json"
        if source_file.exists():
            return
        try:
            # Use motion.mjs inspect to get accurate metadata
            code, out, err = self._run(
                ['node', str(self._motion_mjs), 'inspect', str(self.template_path)],
                self._assistant_dir, timeout=30, env=self._build_env()
            )
            if code == 0 and out.strip():
                data = _json.loads(out.strip())
                source = {
                    "profile": "motiongit.instance/1",
                    "registry": "https://motiongit.com",
                    "recipientAccountId": data.get("recipientAccountId", data.get("accountId", "")),
                    "apiKeyId": data.get("apiKeyId", ""),
                    "templateId": data.get("templateId", ""),
                    "releaseId": data.get("releaseId", ""),
                    "releaseArchiveSha256": data.get("releaseArchiveSha256", ""),
                    "downloadId": data.get("downloadId", ""),
                    "receiptSha256": data.get("receiptSha256", ""),
                }
                source_file.write_text(_json.dumps(source, ensure_ascii=False), encoding='utf-8')
        except Exception:
            pass

    def _read_zip_json(self, name: str) -> dict:
        import json as _json
        with zipfile.ZipFile(self.template_path, 'r') as z:
            return _json.loads(z.read(name).decode('utf-8'))

    @staticmethod
    def _clean_dependency_cache() -> None:
        """Remove stale motiongit dependency caches in TEMP dir."""
        import tempfile
        temp_dir = Path(tempfile.gettempdir())
        for pattern in ['motiongit-dependencies-*', 'motiongit-runtime-overlay-*']:
            for d in temp_dir.glob(pattern):
                try:
                    shutil.rmtree(d, ignore_errors=True)
                except Exception:
                    pass

    @staticmethod
    def _mark_plan_reviewed(plan_dir: Path) -> None:
        """Mark plan output as reviewed (required before prepare)."""
        review_path = plan_dir / "review.json"
        if not review_path.exists():
            return
        try:
            import json as _json
            data = _json.loads(review_path.read_text(encoding='utf-8'))
            data["reviewed"] = True
            review_path.write_text(_json.dumps(data, ensure_ascii=False), encoding='utf-8')
        except Exception:
            pass

    @staticmethod
    def _mark_owner_effects_accepted(project_dir: Path) -> None:
        """Mark owner effects acceptance in prepare output."""
        origin_path = project_dir / "motiongit-origin.json"
        if not origin_path.exists():
            return
        try:
            import json as _json
            data = _json.loads(origin_path.read_text(encoding='utf-8'))
            data["ownerEffectsAcceptance"] = "accepted"
            origin_path.write_text(_json.dumps(data, ensure_ascii=False), encoding='utf-8')
        except Exception:
            pass

    @staticmethod
    def _mark_preview_reviewed(project_dir: Path) -> None:
        """Mark preview as reviewed (required before render)."""
        preview_path = project_dir / "preview.json"
        if not preview_path.exists():
            return
        try:
            import json as _json
            data = _json.loads(preview_path.read_text(encoding='utf-8'))
            data["reviewed"] = True
            preview_path.write_text(_json.dumps(data, ensure_ascii=False), encoding='utf-8')
        except Exception:
            pass

    def _run_motion(self, operation: str, extra_args: list,
                    timeout: int = 300) -> tuple[int, str, str]:
        """Run motion.mjs with the given operation.

        Correct CLI format:
          node motion.mjs run <template.motion> --instance <dir> <operation> [args]
        Note: --instance MUST come BEFORE operation.
        """
        cmd = ['node', str(self._motion_mjs), 'run', str(self.template_path),
               '--instance', str(self.instance_dir), operation] + extra_args
        return self._run(cmd, self._assistant_dir, timeout=timeout,
                         env=self._build_env())

    def render(self, params: dict, output_path: str,
               progress_cb: Callable = None) -> dict:
        if not self.extracted_dir:
            self.prepare()

        if self.runtime_generation == 'v2':
            return self._render_v2(params, output_path, progress_cb)
        return self._render_v1(params, output_path, progress_cb)

    # ------------------------------------------------------------------
    # v1 runtime: motion.mjs run --workflow motion-kit
    # ------------------------------------------------------------------
    def _render_v1(self, params: dict, output_path: str,
                   progress_cb: Callable = None) -> dict:
        t0 = time.time()
        out_path = Path(output_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Write params
        params_file = self.instance_dir / "params.json"
        params_file.write_text(json.dumps(params, ensure_ascii=False), encoding='utf-8')
        inputs_dir = self.instance_dir / "inputs"
        inputs_dir.mkdir(exist_ok=True)

        # 1. doctor 环境检查
        if progress_cb:
            progress_cb("env", 5, "环境检查...")
        code, out, err = self._run_motion('doctor', [], timeout=60)
        has_missing = 'MISSING' in out
        if code != 0 and not has_missing:
            # doctor failed but no missing - might be cache issue, proceed
            pass

        # 2. setup (only if doctor found missing dependencies)
        if has_missing or code != 0:
            if progress_cb:
                progress_cb("env", 10, "安装依赖...")
            self._clean_dependency_cache()
            code, out, err = self._run_motion('setup', ['--workflow', 'motion-kit'], timeout=300)
            if code != 0:
                # "Dependency cache exists" means deps are already installed - skip
                if 'Dependency cache exists' in err or 'Dependency cache exists' in out:
                    pass  # Dependencies already installed, proceed
                else:
                    raise RenderError("env", "setup 失败", (err + out)[-500:])

        # 3. check-params
        if progress_cb:
            progress_cb("plan", 20, "验证参数...")
        code, out, err = self._run_motion('check-params',
            [str(params_file), '--inputs', str(inputs_dir)], timeout=60)
        if code != 0:
            raise RenderError("plan", "参数验证失败", (err + out)[-500:])

        # 4. plan
        if progress_cb:
            progress_cb("plan", 30, "生成制作计划...")
        plan_dir = self.instance_dir / "plan"
        if plan_dir.exists():
            shutil.rmtree(plan_dir)
        code, out, err = self._run_motion('plan',
            [str(params_file), '--inputs', str(inputs_dir), '--out', str(plan_dir)],
            timeout=120)
        if code != 0:
            raise RenderError("plan", "plan 失败", (err + out)[-500:])
        # Mark plan as reviewed (required before prepare)
        self._mark_plan_reviewed(plan_dir)

        # 5. prepare
        if progress_cb:
            progress_cb("prepare", 45, "构建渲染项目...")
        project_dir = self.instance_dir / "project"
        if project_dir.exists():
            shutil.rmtree(project_dir)
        code, out, err = self._run_motion('prepare',
            [str(params_file), '--inputs', str(inputs_dir),
             '--plan', str(plan_dir), '--out', str(project_dir)],
            timeout=180)
        if code != 0:
            raise RenderError("prepare", "prepare 失败", (err + out)[-500:])
        # Mark owner effects as accepted
        self._mark_owner_effects_accepted(project_dir)

        # 6. preview (optional)
        if progress_cb:
            progress_cb("preview", 60, "生成预览图...")
        code, out, err = self._run_motion('preview',
            [str(project_dir), '--times', '2.0'], timeout=120)
        # Mark preview as reviewed (required before render)
        self._mark_preview_reviewed(project_dir)

        # 7. render (output must be inside instance_dir)
        if progress_cb:
            progress_cb("render", 70, "渲染视频...")
        render_out = self.instance_dir / "render_out" / "output.mp4"
        render_out.parent.mkdir(parents=True, exist_ok=True)
        code, out, err = self._run_motion('render',
            [str(project_dir), '--out', str(render_out)], timeout=600)
        if code != 0:
            raise RenderError("render", "渲染失败", (err + out)[-500:])

        # Copy to desired output path
        if render_out != out_path and render_out.exists():
            shutil.copy2(render_out, out_path)

        elapsed = time.time() - t0
        if progress_cb:
            progress_cb("done", 100, f"完成 ({elapsed:.1f}s)")

        return {
            'success': True,
            'output_path': str(out_path),
            'elapsed_s': round(elapsed, 1),
            'format': 'personal-download-v1',
            'runtime_generation': 'v1',
            'project_dir': str(project_dir),
        }

    # ------------------------------------------------------------------
    # v2 runtime: motion.mjs run (auto-route) - split_root / canvas_split_root
    # E7-verified sequence (2026-10-28):
    #   setup(no --workflow) -> check-params -> plan -> prepare(@vault)
    #   -> preview -> render(no --workers)
    # ------------------------------------------------------------------
    def _render_v2(self, params: dict, output_path: str,
                   progress_cb: Callable = None) -> dict:
        t0 = time.time()
        out_path = Path(output_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Write params
        params_file = self.instance_dir / "params.json"
        params_file.write_text(json.dumps(params, ensure_ascii=False), encoding='utf-8')
        inputs_dir = self.instance_dir / "inputs"
        inputs_dir.mkdir(exist_ok=True)

        # 1. setup - v2 auto-routes; --workflow/--cache are REJECTED
        if progress_cb:
            progress_cb("env", 10, "安装依赖(v2)...")
        self._clean_dependency_cache()
        code, out, err = self._run_motion('setup', [], timeout=300)
        if code != 0:
            if 'Dependency cache exists' in err or 'Dependency cache exists' in out:
                pass  # already installed
            else:
                raise RenderError("env", "setup(v2) 失败", (err + out)[-500:])
        # Capture decrypted locale core (contracts/adapter) to instance/core
        self._capture_v2_core()

        # 2. check-params - same CLI shape as v1
        if progress_cb:
            progress_cb("plan", 20, "验证参数...")
        code, out, err = self._run_motion('check-params',
            [str(params_file), '--inputs', str(inputs_dir)], timeout=60)
        if code != 0:
            raise RenderError("plan", "参数验证失败(v2)", (err + out)[-600:])

        # 3. plan
        if progress_cb:
            progress_cb("plan", 30, "生成制作计划...")
        plan_dir = self.instance_dir / "plan"
        if plan_dir.exists():
            shutil.rmtree(plan_dir)
        code, out, err = self._run_motion('plan',
            [str(params_file), '--inputs', str(inputs_dir), '--out', str(plan_dir)],
            timeout=180)
        if code != 0:
            raise RenderError("plan", "plan 失败(v2)", (err + out)[-800:])
        self._mark_plan_reviewed(plan_dir)

        # 4. prepare - canvas consumes plan vault via '@vault'; split-root
        #    consumes raw params + --inputs (E7-verified CLI shapes)
        if progress_cb:
            progress_cb("prepare", 45, "构建渲染项目...")
        project_dir = self.instance_dir / "project"
        if project_dir.exists():
            shutil.rmtree(project_dir)
        if self._v2_kind == 'canvas-split-root':
            prepare_args = ['@vault', '--plan', str(plan_dir), '--out', str(project_dir)]
        else:
            prepare_args = [str(params_file), '--inputs', str(inputs_dir),
                            '--plan', str(plan_dir), '--out', str(project_dir)]
        code, out, err = self._run_motion('prepare', prepare_args, timeout=240)
        if code != 0:
            raise RenderError("prepare", "prepare 失败(v2)", (err + out)[-800:])
        self._mark_owner_effects_accepted(project_dir)

        # 5. preview
        if progress_cb:
            progress_cb("preview", 60, "生成预览图...")
        # canvas runtime preview uses explicit times; split-root uses narration beats
        if self._v2_kind == 'canvas-split-root':
            preview_times = '1,7,18,33,47,56'
        else:
            preview_times = '0,2.5,5.2,7.9,10.5'
        code, out, err = self._run_motion('preview',
            [str(project_dir), '--times', preview_times], timeout=180)
        self._mark_preview_reviewed(project_dir)

        # 6. render - output must be inside instance; no --workers for canvas
        if progress_cb:
            progress_cb("render", 70, "渲染视频...")
        render_out = self.instance_dir / "render_out" / "output.mp4"
        render_out.parent.mkdir(parents=True, exist_ok=True)
        code, out, err = self._run_motion('render',
            [str(project_dir), '--out', str(render_out)], timeout=1800)
        if code != 0:
            raise RenderError("render", "渲染失败(v2)", (err + out)[-800:])

        # Copy to desired output path
        if render_out != out_path and render_out.exists():
            shutil.copy2(render_out, out_path)

        elapsed = time.time() - t0
        if progress_cb:
            progress_cb("done", 100, f"完成 ({elapsed:.1f}s)")

        return {
            'success': True,
            'output_path': str(out_path),
            'elapsed_s': round(elapsed, 1),
            'format': 'personal-download-v1',
            'runtime_generation': 'v2',
            'v2_kind': self._v2_kind,
            'project_dir': str(project_dir),
        }
