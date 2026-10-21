#!/usr/bin/env python3
"""O.motion CLI - Command line interface for .motion template operations.

Usage:
  omotion render <template> --params <params.json> --output <out.mp4>
  omotion inspect <template>
  omotion validate <template> --params <params.json>
  omotion batch <tasklist.json>
"""

import argparse
import json
import sys
import time
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.template_parser import detect_format, parse_template
from core.param_manager import ParamSchema
from core.renderer import RenderPipeline, RenderError
from core.quality_gate import QualityGate, CheckStatus


def _load_params(params_path: str) -> dict:
    """Load params from a JSON file."""
    path = Path(params_path)
    if not path.exists():
        print(f"❌ 参数文件不存在: {params_path}", file=sys.stderr)
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_render(args):
    """Render a .motion template with full quality gate pipeline."""
    template = args.template
    output = args.output or "output.mp4"

    # Load params
    if args.params:
        params = _load_params(args.params)
    else:
        print("❌ 必须提供 --params 参数文件", file=sys.stderr)
        sys.exit(1)

    print(f"🎬 渲染模板: {template}")
    print(f"📄 参数文件: {args.params}")
    print(f"🎯 输出: {output}")

    # Step 1: Parse template
    t0 = time.time()
    try:
        fmt = detect_format(template)
        info = parse_template(template)
        print(f"✅ 模板解析: {info.name} ({fmt})")
    except Exception as e:
        print(f"❌ 模板解析失败: {e}", file=sys.stderr)
        sys.exit(1)

    # Step 2: Validate params (if schema available)
    schema = ParamSchema.from_template(template)
    if schema:
        errors = schema.validate(params)
        if errors:
            print(f"⚠️  参数校验警告 ({len(errors)} 项):")
            for err in errors[:5]:
                print(f"   - {err}")
            if len(errors) > 5:
                print(f"   ... 还有 {len(errors) - 5} 项")
        else:
            print(f"✅ 参数校验通过")

    # Step 3: Render
    instance_dir = str(Path(output).parent / "_render_work" / Path(template).stem)
    pipeline = RenderPipeline(template, instance_dir)

    def progress(stage, pct, msg):
        print(f"  [{stage:8s}] {pct:3d}% - {msg}")

    try:
        result = pipeline.render(params, output, progress_cb=progress)
        print(f"✅ 渲染完成 ({result['elapsed_s']}s)")
    except RenderError as e:
        print(f"❌ 渲染失败 [{e.stage}]: {e.message}", file=sys.stderr)
        if e.detail:
            print(f"   详情: {e.detail[:300]}", file=sys.stderr)
        sys.exit(1)

    # Step 4: Quality gate
    gate = QualityGate()
    expected = {
        "width": info.output_width,
        "height": info.output_height,
        "duration": info.duration_seconds,
        "require_audio": fmt == "personal-download-v1",
    }
    report = gate.check(template, params, output, result, expected)

    print(f"\n📊 质量报告: {report.status.value.upper()}")
    print(f"   通过: {report.summary['passed']}, "
          f"警告: {report.summary['warnings']}, "
          f"失败: {report.summary['failed']}")

    for c in report.checks:
        if c.status != CheckStatus.PASS:
            icon = "⚠️" if c.status == CheckStatus.WARN else "❌"
            print(f"   {icon} [{c.stage}] {c.name}: {c.message}")

    if report.status == CheckStatus.FAIL:
        print(f"\n❌ 质量门未通过，输出文件已生成但存在严重问题", file=sys.stderr)
        sys.exit(2)

    elapsed = time.time() - t0
    print(f"\n🎉 全部完成! 总耗时 {elapsed:.1f}s")
    print(f"   输出: {output}")


def cmd_inspect(args):
    """Inspect a .motion template and print metadata."""
    template = args.template
    print(f"🔍 检查模板: {template}\n")

    try:
        fmt = detect_format(template)
        info = parse_template(template)
    except Exception as e:
        print(f"❌ 解析失败: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"  格式: {fmt}")
    print(f"  名称: {info.name}")
    print(f"  ID: {info.template_id}")
    print(f"  版本: {info.template_version}")
    print(f"  规范版本: {info.spec_version}")
    print(f"  运行时: {info.runtime_profile}")
    print(f"  入口: {info.runtime_entry}")
    print(f"  输出: {info.output_width}x{info.output_height} @ {info.output_fps}fps")
    print(f"  时长策略: {info.duration_policy}")
    if info.duration_seconds:
        print(f"  声明时长: {info.duration_seconds}s")

    # Try to extract schema
    schema = ParamSchema.from_template(template)
    if schema:
        print(f"\n  参数 Schema:")
        print(f"    必填: {schema.list_required()}")
        print(f"    可选: {schema.list_optional()}")


def cmd_validate(args):
    """Validate template and params without rendering."""
    template = args.template
    print(f"✅ 校验模板: {template}\n")

    gate = QualityGate()

    # Static checks
    static_results = gate.check_static(template)
    for r in static_results:
        icon = "✅" if r.status == CheckStatus.PASS else "❌"
        print(f"  {icon} [static] {r.name}: {r.message}")

    if any(r.status == CheckStatus.FAIL for r in static_results):
        print("\n❌ 静态校验失败", file=sys.stderr)
        sys.exit(1)

    # Params checks
    if args.params:
        params = _load_params(args.params)
        param_results = gate.check_params(template, params)
        for r in param_results:
            icon = "✅" if r.status == CheckStatus.PASS else "❌"
            print(f"  {icon} [params] {r.name}: {r.message}")

        if any(r.status == CheckStatus.FAIL for r in param_results):
            print("\n❌ 参数校验失败", file=sys.stderr)
            sys.exit(1)

    print("\n✅ 校验通过")


def cmd_batch(args):
    """Batch render from a task list JSON file."""
    tasklist = args.tasklist
    print(f"📦 批量渲染: {tasklist}")
    print("⚠️  批量渲染功能将在 Phase 1 实现")
    # TODO: Implement batch rendering in Phase 1


def main():
    parser = argparse.ArgumentParser(
        prog="omotion",
        description="O.motion - .motion template authoring & batch production toolkit"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # render
    render_p = subparsers.add_parser("render", help="Render a .motion template")
    render_p.add_argument("template", help="Path to .motion template")
    render_p.add_argument("--params", required=True, help="Path to params JSON file")
    render_p.add_argument("--output", "-o", help="Output MP4 path (default: output.mp4)")
    render_p.add_argument("--preview", action="store_true", help="Low-res preview mode")

    # inspect
    inspect_p = subparsers.add_parser("inspect", help="Inspect a .motion template")
    inspect_p.add_argument("template", help="Path to .motion template")

    # validate
    validate_p = subparsers.add_parser("validate", help="Validate template and params")
    validate_p.add_argument("template", help="Path to .motion template")
    validate_p.add_argument("--params", help="Path to params JSON file")

    # batch
    batch_p = subparsers.add_parser("batch", help="Batch render from task list")
    batch_p.add_argument("tasklist", help="Path to task list JSON")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    commands = {
        "render": cmd_render,
        "inspect": cmd_inspect,
        "validate": cmd_validate,
        "batch": cmd_batch,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
