"""Fragment combiner - compose multiple fragments into a new template.

组合模板 = 容器模板：内含 fragments/<id>/ 子片段（每个是可独立渲染的完整文件集）
+ 组合调度 adapter.py。渲染时按 scenes 参数逐段调用子片段 adapter 渲染，
再以 FFmpeg concat 拼接为完整视频。

组合模板结构:
    combined.motion (ZIP)
    ├── motion.json          # standard 格式，runtime: adapter.py
    ├── params.schema.json   # 组合参数: duration + scenes[{fragmentId, ...片段参数}]
    ├── adapter.py           # 组合调度器（plan/prepare/render）
    ├── fragments/<id>/      # 子片段完整内容（含各自 adapter.py / core / params.schema.json）
    └── licenses/
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

COMBINER_SPEC_VERSION = "1.0.0"

# 组合模板内嵌的调度 adapter（模板运行时执行）
_COMBINER_ADAPTER = r'''# /// script
# requires-python = ">=3.11"
# dependencies = ["jsonschema"]
# ///
"""组合模板调度 adapter：按 scenes 逐段渲染子片段并 FFmpeg concat。

协议（omotion-core StandardRenderEngine 驱动）:
  python adapter.py plan    --input <input.json>
  python adapter.py prepare --input <input.json> --out <project_dir>
  python adapter.py render  --instance <project_dir> --out <output_path>
"""
from pathlib import Path
import argparse, json, shutil, subprocess, sys

ROOT = Path(__file__).resolve().parent
FRAGMENTS = ROOT / "fragments"


def read(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def save(p, o):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(o, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate(params):
    from jsonschema import Draft202012Validator
    schema = read(ROOT / "params.schema.json")
    errors = sorted(Draft202012Validator(schema).iter_errors(params),
                    key=lambda e: str(list(e.path)))
    if errors:
        raise ValueError("Invalid input: " + errors[0].message)
    return params


def build_fragment_params(frag_dir, scene) -> dict:
    """从 scene 元素构造子片段渲染参数：
    剔除 fragmentId，补 const/default/enum/类型兜底，scenes 裁剪为单场景。"""
    frag_schema = read(frag_dir / "params.schema.json")
    props = frag_schema.get("properties", {})
    params = {}
    for key, prop in props.items():
        if "const" in prop:
            params[key] = prop["const"]
        elif "default" in prop:
            params[key] = prop["default"]
        elif "enum" in prop:
            params[key] = prop["enum"][0]
        else:
            t = prop.get("type")
            if t == "string":
                params[key] = str(scene.get(key, "片段"))
            elif t == "number":
                params[key] = scene.get(key, 5.0)
            elif t == "integer":
                params[key] = scene.get(key, 1)
            elif t == "boolean":
                params[key] = scene.get(key, False)
            elif t == "object":
                params[key] = scene.get(key, {})
            elif t == "array":
                params[key] = scene.get(key, [])
    for key, value in scene.items():
        if key == "fragmentId":
            continue
        if key in props:
            params[key] = value
    # scenes 裁剪为单场景（片段模板 scenes 通常为数组）
    if "scenes" in props:
        if ("scenes" in params and isinstance(params["scenes"], list)
                and params["scenes"]):
            params["scenes"] = params["scenes"][:1]
        else:
            params["scenes"] = [{"id": "S01", "title": str(params.get("title", "片段"))}]
    return params


def run_fragment_adapter(frag_dir, cmd, args_list, timeout=600):
    result = subprocess.run(
        ["uv", "run", "adapter.py", cmd] + args_list,
        cwd=str(frag_dir), capture_output=True, timeout=timeout,
        env={**__import__("os").environ, "PYTHONUTF8": "1"},
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout.decode("utf-8", "replace"))
        sys.stderr.write(result.stderr.decode("utf-8", "replace"))
        sys.exit(1)


def ensure_fragment_npm(frag_dir):
    """子片段若有 package.json 且缺 node_modules，先 npm ci 补齐依赖。"""
    if (frag_dir / "node_modules").exists():
        return
    if not (frag_dir / "package.json").exists():
        return
    npm_cmd = "npm.cmd" if __import__("os").name == "nt" else "npm"
    result = subprocess.run(
        [npm_cmd, "ci", "--no-audit", "--no-fund"],
        cwd=str(frag_dir), capture_output=True, timeout=300,
        env={**__import__("os").environ, "PYTHONUTF8": "1"},
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout.decode("utf-8", "replace"))
        sys.stderr.write(result.stderr.decode("utf-8", "replace"))
        sys.exit(1)


def cmd_plan(args):
    params = read(args.input)
    validate(params)
    for i, scene in enumerate(params.get("scenes", [])):
        fid = scene.get("fragmentId")
        if not fid:
            raise ValueError(f"scenes[{i}] 缺少 fragmentId")
        if not (FRAGMENTS / fid).exists():
            raise ValueError(f"scenes[{i}] fragmentId '{fid}' 不存在于 fragments/")
    print("PLAN_OK")


def cmd_prepare(args):
    params = read(args.input)
    validate(params)
    project_dir = Path(args.out).resolve()
    if project_dir.exists():
        print(f"错误：project_dir 已存在 {project_dir}", file=sys.stderr)
        sys.exit(1)
    project_dir.mkdir(parents=True)
    save(project_dir / "composition.json", {
        "scenes": params.get("scenes", []),
        "duration": params.get("duration", 0),
    })
    print("PREPARE_OK")


def cmd_render(args):
    project_dir = Path(args.instance).resolve()
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    composition = read(project_dir / "composition.json")
    scenes = composition["scenes"]

    work = project_dir / "segments"
    work.mkdir(parents=True, exist_ok=True)
    segments = []
    for i, scene in enumerate(scenes):
        fid = scene["fragmentId"]
        frag_dir = FRAGMENTS / fid
        seg_dir = work / f"seg_{i}"
        seg_dir.mkdir(parents=True, exist_ok=True)

        # 子片段依赖就绪（npm ci）
        ensure_fragment_npm(frag_dir)

        frag_params = build_fragment_params(frag_dir, scene)
        input_file = seg_dir / "input.json"
        save(input_file, frag_params)

        # 子片段 plan/prepare
        run_fragment_adapter(frag_dir, "plan", ["--input", str(input_file)], timeout=120)
        proj_dir = seg_dir / "project"
        run_fragment_adapter(frag_dir, "prepare",
                             ["--input", str(input_file), "--out", str(proj_dir)],
                             timeout=180)
        # 子片段 render
        seg_mp4 = seg_dir / f"seg_{i}.mp4"
        if seg_mp4.exists():
            seg_mp4.unlink()
        run_fragment_adapter(frag_dir, "render",
                             ["--instance", str(proj_dir), "--out", str(seg_mp4)],
                             timeout=600)
        if not seg_mp4.exists():
            print(f"片段 {fid} 未产出视频", file=sys.stderr)
            sys.exit(1)
        segments.append(str(seg_mp4))
        print(f"  段 {i + 1}/{len(scenes)}: {fid} -> {seg_mp4.name}")

    # FFmpeg concat
    list_file = work / "concat.txt"
    list_file.write_text(
        "\n".join(f"file '{Path(s).as_posix()}'" for s in segments) + "\n",
        encoding="utf-8")
    if out_path.exists():
        out_path.unlink()
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
           "-i", str(list_file), "-c", "copy", str(out_path)]
    result = subprocess.run(cmd, capture_output=True, timeout=600)
    if result.returncode != 0:
        sys.stderr.write(result.stdout.decode("utf-8", "replace"))
        sys.stderr.write(result.stderr.decode("utf-8", "replace"))
        sys.exit(1)
    if not out_path.exists():
        print("FFmpeg concat 未产生输出", file=sys.stderr)
        sys.exit(1)
    # 清理中间段
    shutil.rmtree(work, ignore_errors=True)
    print("RENDER_OK")


def main():
    parser = argparse.ArgumentParser(prog="adapter.py")
    sub = parser.add_subparsers(dest="command")
    p1 = sub.add_parser("plan")
    p1.add_argument("--input", required=True)
    p2 = sub.add_parser("prepare")
    p2.add_argument("--input", required=True)
    p2.add_argument("--out", required=True)
    p3 = sub.add_parser("render")
    p3.add_argument("--instance", required=True)
    p3.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.command == "plan":
        cmd_plan(args)
    elif args.command == "prepare":
        cmd_prepare(args)
    elif args.command == "render":
        cmd_render(args)
    else:
        print("未知命令", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
'''

# 组合模板 params.schema.json（遵循 standard 参数规范：mode/language/layout + duration/scenes）
_COMBINER_SCHEMA = {
    "type": "object",
    "required": ["mode", "language", "layout", "duration", "scenes"],
    "additionalProperties": False,
    "properties": {
        "mode": {"const": "user-media", "description": "组合模板模式（固定）"},
        "language": {"type": "string", "enum": ["zh", "en"], "description": "组合输出语言"},
        "layout": {"type": "string", "enum": ["landscape", "portrait"],
                   "description": "组合输出画幅"},
        "duration": {"type": "number", "minimum": 1, "maximum": 3600,
                     "description": "组合总时长（各段实际时长之和）"},
        "scenes": {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "description": "片段序列：每个元素引用一个子片段并透传其参数",
            "items": {
                "type": "object",
                "required": ["fragmentId"],
                "properties": {
                    "fragmentId": {"type": "string", "description": "子片段ID（fragments/<id>）"},
                    "title": {"type": "string", "maxLength": 45, "description": "段标题（透传片段 title）"},
                    "duration": {"type": "number", "minimum": 1, "maximum": 600,
                                 "description": "段时长（透传片段 duration）"},
                },
                "additionalProperties": True,
            },
        },
    },
}

_MOTION_JSON = {
    "$schema": "https://motiongit.com/schema/motion/0.1.json",
    "format": "standard",
    "specVersion": "0.1.0-draft.1",
    "runtime": {"profile": "kinetic-user-core/1", "entry": "adapter.py"},
    "parameters": {"schema": "params.schema.json"},
    "output": {
        "width": 1920, "height": 1080,
        "fps": {"num": 60, "den": 1},
        "durationPolicy": "fit-content",
        "layouts": {"landscape": [1920, 1080]},
    },
    "permissions": {"network": False, "read": ["package", "instance-inputs"],
                    "write": ["instance-output"]},
}


class FragmentCombiner:
    """Combine fragments on a timeline to create a new template."""

    def combine(self, fragments: list[dict], output_path: str) -> str:
        """Combine fragments into a new .motion template.

        Args:
            fragments: [{id, path, ...}] — id 为片段标识（目录名），path 为
                       fragment 目录或 .motion 包路径
            output_path: 组合模板输出路径（.motion）

        Returns:
            组合模板路径（output_path）

        Raises:
            ValueError: 片段缺失/格式错误/ID 冲突
        """
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        combined_dir = out.parent / f"_{out.stem}_combine"
        if combined_dir.exists():
            shutil.rmtree(combined_dir)

        fragments_root = combined_dir / "fragments"
        fragments_root.mkdir(parents=True)

        ids = []
        for frag in fragments:
            fid = frag.get("id")
            fpath = Path(frag.get("path", ""))
            if not fid or not fpath.exists():
                raise ValueError(f"片段缺少 id 或路径不存在: {frag}")
            if fid in ids:
                raise ValueError(f"片段 ID 冲突: {fid}")
            ids.append(fid)

            target = fragments_root / fid
            target.mkdir(parents=True)
            if fpath.is_dir():
                shutil.copytree(fpath, target, dirs_exist_ok=True)
            else:  # .motion ZIP
                with zipfile.ZipFile(fpath, "r") as z:
                    z.extractall(target)

            # 子片段必须自带 adapter（独立渲染能力）
            if not (target / "adapter.py").exists():
                raise ValueError(f"片段 {fid} 缺少 adapter.py，无法独立渲染")

        # 组合模板元数据
        motion = dict(_MOTION_JSON)
        motion["template"] = {
            "id": out.stem,
            "version": "0.1.0",
            "name": f"{out.stem}（组合 {len(ids)} 片段）",
            "status": "open-beta",
        }
        motion["ai"] = {
            "summary": f"由 {len(ids)} 个片段组合而成: {', '.join(ids)}",
        }
        motion["dependencies"] = [{"fragments": ids}]

        (combined_dir / "motion.json").write_text(
            json.dumps(motion, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (combined_dir / "params.schema.json").write_text(
            json.dumps(_COMBINER_SCHEMA, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        (combined_dir / "adapter.py").write_text(
            _COMBINER_ADAPTER, encoding="utf-8")

        # 组合模板根需有 package.json + lockfile（engine 的 npm ci 依赖）
        self._ensure_package(combined_dir)

        # 打包
        self._pack(combined_dir, out)
        shutil.rmtree(combined_dir, ignore_errors=True)
        return str(out)

    @staticmethod
    def _ensure_package(combined_dir: Path) -> None:
        """生成组合模板根的 package.json + package-lock.json（空依赖）。"""
        pkg = {
            "name": combined_dir.name,
            "version": "0.1.0",
            "private": True,
            "dependencies": {},
        }
        (combined_dir / "package.json").write_text(
            json.dumps(pkg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        lock = combined_dir / "package-lock.json"
        if not lock.exists():
            # Windows 下 npm 实际为 npm.cmd；subprocess 直接调用需带 .cmd
            npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
            result = subprocess.run(
                [npm_cmd, "install", "--package-lock-only", "--no-audit",
                 "--no-fund", "--ignore-scripts"],
                cwd=str(combined_dir), capture_output=True, timeout=180)
            if result.returncode != 0 or not lock.exists():
                # lockfile 生成失败时写入最小合法 lockfile（npm ci 可接受）
                lock.write_text(json.dumps({
                    "name": combined_dir.name,
                    "version": "0.1.0",
                    "lockfileVersion": 3,
                    "requires": True,
                    "packages": {"": {"name": combined_dir.name, "version": "0.1.0"}},
                }, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _pack(combined_dir: Path, motion_file: Path) -> None:
        """打包组合目录为 .motion（排除 node_modules/临时文件）。"""
        exclude_dirs = {"node_modules", "__pycache__", ".git"}
        if motion_file.exists():
            motion_file.unlink()
        with zipfile.ZipFile(motion_file, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(combined_dir.rglob("*")):
                if p.is_dir():
                    continue
                rel = p.relative_to(combined_dir)
                if any(part in exclude_dirs for part in rel.parts):
                    continue
                z.write(p, rel.as_posix())
