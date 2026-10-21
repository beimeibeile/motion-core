"""Render engine - wraps template-specific render backends.

Supports:
- standard format: adapter.py (uv run) -> render.mjs -> mix.py
- personal-download-v1 (v1 runtime): motion.mjs run --workflow motion-kit
- personal-download-v1 (v2 runtime): split_root.py / canvas_split_root.py (NOT YET SUPPORTED - Windows symlink bug)
"""

import json
import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Callable, Optional

_SHELL = os.name == 'nt'


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
        """
        result = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True,
            timeout=timeout, shell=_SHELL, env=env
        )
        stdout = result.stdout.decode('utf-8', errors='replace') if result.stdout else ''
        stderr = result.stderr.decode('utf-8', errors='replace') if result.stderr else ''
        return result.returncode, stdout, stderr

    @staticmethod
    def _build_env() -> dict:
        """Build runtime environment: UTF-8 + system Chrome path."""
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["LANG"] = "en_US.UTF-8"
        # Find system Chrome
        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
        for p in chrome_paths:
            if Path(p).exists():
                env["CHROME_PATH"] = p
                break
        return env

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
    """Render engine for personal-download-v1 .motion templates (v1 runtime).

    Pipeline: motion.mjs run --workflow motion-kit
      doctor -> setup -> check-params -> plan -> prepare -> preview -> render

    NOTE: Only supports v1 runtime (motion.mjs + --workflow).
    v2 runtime (split_root.py / canvas_split_root.py) is NOT supported yet
    due to Windows symlink bug [WinError 183].
    """

    def __init__(self, template_path: str, instance_dir: str):
        super().__init__(template_path, instance_dir)
        self._motion_mjs: Optional[Path] = None
        self._assistant_dir: Optional[Path] = None

    def prepare(self) -> None:
        self._extract()
        self._assistant_dir = self.extracted_dir / "assistant"
        self._motion_mjs = self._assistant_dir / "motion.mjs"
        if not self._motion_mjs.exists():
            raise RenderError("env", "缺少 assistant/motion.mjs")
        # CRITICAL: Patch motion.mjs to pass all env vars to child processes
        # (otherwise PYTHONUTF8/CHROME_PATH are lost, causing GBK decode errors)
        self._patch_motion_mjs(self._motion_mjs)
        # Prepare instance dir structure
        self.instance_dir.mkdir(parents=True, exist_ok=True)
        (self.instance_dir / "inputs").mkdir(exist_ok=True)
        # Write motiongit-source.json (required by motion.mjs)
        self._write_source_metadata()

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
                elif 'unrecognized arguments' in err or 'split_root' in err:
                    raise RenderError("env",
                        "该模板使用新版运行时(v2)，当前引擎暂不支持（Windows符号链接bug）",
                        err[-300:])
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
