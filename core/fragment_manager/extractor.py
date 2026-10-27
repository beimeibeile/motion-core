"""Fragment extractor - extract reusable fragments from existing templates.

片段 = 可独立渲染的最小动画单元。从 standard 格式模板提取：
1. 解压模板为 fragment 目录（保留完整可渲染文件集：motion.json / params.schema.json /
   adapter.py / render.mjs / core/ ...）
2. 校验并记录场景范围（sceneRange）
3. 生成 fragment.json（规格 §6 片段定义：id/name/category/duration/runtime/parameters/
   dependencies/compatibility）

参数裁剪语义：standard 模板的 scenes 由渲染参数决定，schema 层面保持 items 定义不变；
场景范围记录于 fragment.json.sceneRange，由验证器/组合器据此裁剪渲染参数。
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from core.template_parser import detect_format, parse_template

FRAGMENT_SPEC_VERSION = "1.0.0"


class FragmentExtractor:
    """Extract independent animation scenes from templates as fragments."""

    def extract(self, template_path: str, scene_range: tuple[int, int],
                output_dir: str) -> str:
        """Extract a scene range from template as a fragment.

        Args:
            template_path: .motion 模板路径（standard 格式 ZIP）
            scene_range: (start, end) 闭区间场景索引；模板无 scenes 参数时视为 (0, 0)
            output_dir: fragment 输出根目录

        Returns:
            提取的 fragment 目录路径

        Raises:
            ValueError: 格式不支持 / 场景范围越界
        """
        tpl = Path(template_path)
        if not tpl.exists():
            raise ValueError(f"模板不存在: {tpl}")

        fmt = detect_format(str(tpl))
        if fmt != "standard":
            raise ValueError(f"仅支持 standard 格式模板，当前格式: {fmt}")

        info = parse_template(str(tpl))
        schema = info.parameters_schema or {}
        scenes_prop = schema.get("properties", {}).get("scenes", {})
        has_scenes = "scenes" in schema.get("properties", {})
        max_scenes = int(scenes_prop.get("maxItems", 1)) if has_scenes else 1

        start, end = scene_range
        if start < 0 or end < start:
            raise ValueError(f"非法场景范围: [{start}, {end}]")
        if has_scenes and end >= max_scenes:
            raise ValueError(
                f"场景范围越界: [{start}, {end}]，模板 scenes 上限 {max_scenes}")

        # fragment 目录
        frag_dir = Path(output_dir) / f"fragment_{info.template_id}_{start}_{end}"
        if frag_dir.exists():
            shutil.rmtree(frag_dir)
        frag_dir.mkdir(parents=True, exist_ok=True)

        # 解压模板完整文件集到 fragment 目录
        with zipfile.ZipFile(tpl, "r") as z:
            z.extractall(frag_dir)

        # 生成 fragment.json（规格 §6）
        fragment = self._build_fragment_meta(info, start, end, str(tpl))
        (frag_dir / "fragment.json").write_text(
            json.dumps(fragment, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

        return str(frag_dir)

    def _build_fragment_meta(self, info, start: int, end: int,
                             source: str) -> dict:
        """按规格 §6 构造 fragment.json 元数据。"""
        motion = info.raw_motion_json or {}
        template_meta = motion.get("template", {})
        output = motion.get("output", {})

        fps = output.get("fps", 60)
        if isinstance(fps, dict):
            fps = fps.get("num", 60) / fps.get("den", 1)

        duration_seconds = output.get("durationSeconds")
        duration_policy = output.get("durationPolicy", "user-defined")
        # 片段默认时长：模板固定时长或 schema 场景上限（未固定时按 5s 兜底）
        default_dur = float(duration_seconds) if duration_seconds else 5.0

        return {
            "id": f"{info.template_id}-scene{start}-{end}",
            "name": f"{info.name} · 场景 {start + 1}~{end + 1}",
            "version": str(template_meta.get("version", "0.1.0")),
            "category": "scene",
            "tags": ["scene", info.template_id],
            "duration": {
                "min": 1.0,
                "max": max(default_dur * 2, 10.0),
                "default": default_dur,
                "policy": duration_policy,
            },
            "runtime": {
                "profile": info.runtime_profile,
                "entry": info.runtime_entry,
            },
            "parameters": {"schema": "params.schema.json"},
            "dependencies": [],
            "compatibility": [info.template_id],
            "sceneRange": [start, end],
            "source": source,
            "spec": {"fragment": FRAGMENT_SPEC_VERSION},
        }
