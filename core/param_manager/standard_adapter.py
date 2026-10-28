# -*- coding: utf-8 -*-
"""standard 模板（adu-auto-brief/1 契约）参数构建器——打通真实渲染闭环。

背景（2026-10 实测定位）：
  standard 模板（如 motiontemplates/385712 酷炫科技风）的 adapter.py 在
  plan 阶段做**原生约束校验**：
    - 场景（scenes）原生**锁定时长**：非 maxReadingHold 组严禁携带 duration
      （否则 ValueError: Native actions keep their source duration）
    - 选中组的所有 fields 必须非空（needs-binding）
    - 选中组的所有 media roles 必须绑定真实素材
    - 顶层 duration 必须精确等于 Σ 选中组原生时长（±1/120s）

因此通用 build_default_params（schema 兜底生成）必然产出非法契约。
本模块从 core/catalog.json 读取原生约束，生成**契约合法**参数：
  仅允许选择"纯文本组"（media=[] 的 B1-B4 / S 组），自动填 basic/content、
  media 三 none、duration=Σ原生时长。素材绑定组（A/B/C/D/F/S 系列带 media）
  不在本模块范围（需用户提供真实素材，走 producer 参数填充流程）。
"""
from typing import Optional

# B 组（newBasicGroups）：无 media 依赖，字段走 basic
BASIC_REQUIREMENTS = {
    "B1": ["keyword", "explanation"],
    "B2": ["heading", "points"],
    "B3": ["heading", "left", "right", "conclusion"],
    "B4": ["summary", "action"],
}
BASIC_LIMITS = {"keyword": 160, "heading": 160, "left": 160, "right": 160,
                "conclusion": 160, "summary": 160, "action": 160,
                "label": 160}

# 纯文本组（media=[]）候选：B1-B4 + 无素材 S 组
TEXT_ONLY_IDS = ("B1", "B2", "B3", "B4")


def _load_catalog(catalog_path: str) -> dict:
    import json
    with open(catalog_path, encoding="utf-8") as f:
        return json.load(f)


def group_duration(catalog: dict, group_id: str) -> float:
    """组原生时长（秒）。"""
    for g in catalog.get("groups", []):
        if g.get("id") == group_id:
            return round(g["end"] - g["start"], 3)
    raise ValueError(f"Unknown group: {group_id}")


def basic_for_group(group_id: str, topic: str, language: str = "zh") -> dict:
    """按组 requirements 生成 basic 示例文本。"""
    if group_id not in BASIC_REQUIREMENTS:
        return {}
    out = {}
    for key in BASIC_REQUIREMENTS[group_id]:
        if key == "points":
            out[key] = [f"{topic}要点一", f"{topic}要点二"]
        elif key == "keyword":
            out[key] = topic
        elif key == "explanation":
            out[key] = f"围绕「{topic}」展开的清晰说明"
        elif key == "heading":
            out[key] = f"{topic}概览"
        elif key == "conclusion":
            out[key] = f"总结：{topic}值得关注"
        elif key in ("left", "right"):
            out[key] = f"{topic}·{key}侧要点"
        elif key == "summary":
            out[key] = f"{topic}速览"
        elif key == "action":
            out[key] = f"立即了解{topic}"
        else:
            out[key] = topic
    return out


def build_standard_params(catalog_path: str,
                          selected: Optional[list] = None,
                          title: str = "示例",
                          topic: str = "示例主题",
                          language: str = "zh",
                          duration: Optional[float] = None,
                          presenter_label: str = "") -> dict:
    """构建契约合法的 standard 模板参数。

    Args:
        catalog_path: 模板 core/catalog.json 绝对路径
        selected: 选中组 id（默认 ["B1", "B2"]；仅限 TEXT_ONLY_IDS）
        title: 视频标题（≤45 字符）
        topic: 内容主题（填 basic 示例字段）
        language: zh/en
        duration: 显式总时长（须 == Σ原生时长；默认自动算）
        presenter_label: 演示者标签（≤45）

    Returns:
        完整契约参数（mode/language/layout/duration/title/presenterLabel/
        scenes/content/media/soundEffects/captions/basic）
    """
    catalog = _load_catalog(catalog_path)
    sel = selected or ["B1", "B2"]
    for gid in sel:
        if gid not in TEXT_ONLY_IDS:
            raise ValueError(
                f"组 {gid} 需绑定真实素材（media 依赖），本构建器仅支持 "
                f"纯文本组 {list(TEXT_ONLY_IDS)}；素材组请走 producer 参数填充")
    if language not in ("zh", "en"):
        raise ValueError("language 仅支持 zh/en")

    scenes = [{"id": gid, "title": topic} for gid in sel]
    total = sum(group_duration(catalog, gid) for gid in sel)
    if duration is not None and abs(duration - total) > 1 / 120:
        raise ValueError(
            f"declared duration {duration} 与 Σ 原生时长 {total:.2f}s 不符"
            f"（±1/120s 容差）")
    final_duration = duration if duration is not None else round(total, 3)

    basic = {}
    for gid in sel:
        basic.update(basic_for_group(gid, topic, language))

    return {
        "mode": "user-media",
        "language": language,
        "layout": "landscape",
        "duration": final_duration,
        "title": title,
        "presenterLabel": presenter_label,
        "scenes": scenes,
        "content": {},
        "basic": basic,
        "media": {
            "presenter": {"mode": "none"},
            "voice": {"mode": "none"},
            "music": {"mode": "none"},
        },
        "soundEffects": "native",
        "captions": [],
    }
