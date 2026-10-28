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
    - Basic 表达式文本长度上限（zh）：
        keyword=8 heading=10 left=8 right=8 explanation=42
        conclusion=48 summary=10 action=42 label=32

因此通用 build_default_params（schema 兜底生成）必然产出非法契约。
本模块从 core/catalog.json 读取原生约束，生成**契约合法**参数：
  - 纯文本组（B1-B4）：自动填 basic 文本
  - 素材组（S/A/B/C/D/F 等带 media 的组）：需调用方提供该组 content 文本
    与 media 素材（image 单图 或 video 序列），本模块完成绑定校验。
"""
from typing import Optional

# B 组（newBasicGroups）：无 media 依赖，字段走 basic
BASIC_REQUIREMENTS = {
    "B1": ["keyword", "explanation"],
    "B2": ["heading", "points"],
    "B3": ["heading", "left", "right", "conclusion"],
    "B4": ["summary", "action"],
}
# 模板 adapter.py 原生 Basic 表达式文本长度上限（zh；en = zh*3+12）
BASIC_LIMITS = {"keyword": 8, "heading": 10, "left": 8, "right": 8,
                "explanation": 42, "conclusion": 48, "summary": 10,
                "action": 42, "label": 32}

# 纯文本组（media=[]）候选：B1-B4
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


def group_media_roles(catalog: dict, group_id: str) -> list:
    """组的 media roles（如 S11 -> ['thumbnail']）。"""
    for g in catalog.get("groups", []):
        if g.get("id") == group_id:
            return list(g.get("media", []) or [])
    raise ValueError(f"Unknown group: {group_id}")


def validate_basic_text(key: str, value, language: str = "zh") -> None:
    """校验 basic 表达式文本不超过模板原生容量。"""
    limit = BASIC_LIMITS.get(key)
    if limit is None or not isinstance(value, str):
        return
    cap = limit if language == "zh" else limit * 3 + 12
    if len(value) > cap or len(value.split("\n")) > 2:
        raise ValueError(
            f"Basic expression text exceeds capacity: {key} "
            f"(limit {cap} for {language})")


def basic_for_group(group_id: str, topic: str, language: str = "zh") -> dict:
    """按组 requirements 生成 basic 示例文本（受原生容量约束）。"""
    if group_id not in BASIC_REQUIREMENTS:
        return {}
    out = {}
    for key in BASIC_REQUIREMENTS[group_id]:
        if key == "points":
            out[key] = [f"{topic[:6]}要点一", f"{topic[:6]}要点二"]
        elif key == "keyword":
            out[key] = topic[: BASIC_LIMITS["keyword"]]
        elif key == "explanation":
            out[key] = f"围绕「{topic[:16]}」展开的说明"[: BASIC_LIMITS["explanation"]]
        elif key == "heading":
            out[key] = f"{topic[:6]}概览"[: BASIC_LIMITS["heading"]]
        elif key == "conclusion":
            out[key] = f"总结：{topic[:20]}值得关注"[: BASIC_LIMITS["conclusion"]]
        elif key in ("left", "right"):
            out[key] = f"{topic[:5]}·{key}侧"[: BASIC_LIMITS[key]]
        elif key == "summary":
            out[key] = f"{topic[:6]}速览"[: BASIC_LIMITS["summary"]]
        elif key == "action":
            out[key] = f"立即了解{topic[:36]}"[: BASIC_LIMITS["action"]]
        else:
            out[key] = topic
        validate_basic_text(key, out[key], language)
    return out


def build_standard_params(catalog_path: str,
                          selected: Optional[list] = None,
                          title: str = "示例",
                          topic: str = "示例主题",
                          language: str = "zh",
                          duration: Optional[float] = None,
                          presenter_label: str = "",
                          content: Optional[dict] = None,
                          media: Optional[dict] = None,
                          captions: Optional[list] = None,
                          sound_effects: str = "native") -> dict:
    """构建契约合法的 standard 模板参数（支持纯文本组 + 素材组）。

    Args:
        catalog_path: 模板 core/catalog.json 绝对路径
        selected: 选中组 id（默认 ["B1", "B2"]；素材组需一并提供 content/media）
        title: 视频标题（≤45 字符）
        topic: 内容主题（填 B 组 basic 示例字段）
        language: zh/en
        duration: 显式总时长（须 == Σ原生时长；默认自动算）
        presenter_label: 演示者标签（≤45）
        content: S 组/其余组必填字段文本，形如 {"S11.text001": "..."}
                未提供素材组 content 时自动尝试 basic_for_group（仅 B 组）
        media: 素材绑定，形如 {"thumbnail": "/abs/path.jpg"} 或
               {"thumbnail": {"mode": "image", "file": "/abs/path.jpg"}}
        captions: 字幕数组（默认 []）
        sound_effects: native / off

    Returns:
        完整契约参数（mode/language/layout/duration/title/presenterLabel/
        scenes/content/media/soundEffects/captions/basic）
    """
    catalog = _load_catalog(catalog_path)
    sel = list(selected or ["B1", "B2"])
    if language not in ("zh", "en"):
        raise ValueError("language 仅支持 zh/en")

    scenes = []
    basic = {}
    group_content = {}
    media_out = {
        "presenter": {"mode": "none"},
        "voice": {"mode": "none"},
        "music": {"mode": "none"},
    }
    media = media or {}

    for gid in sel:
        roles = group_media_roles(catalog, gid)
        if not roles:
            # 纯文本组：basic 填充
            basic.update(basic_for_group(gid, topic, language))
        else:
            # 素材组：必须绑定全部 media roles（image 单图模式）
            for role in roles:
                if role not in media:
                    raise ValueError(
                        f"组 {gid} 需绑定素材 role '{role}'；"
                        f"请通过 media 参数提供（image 或 video）")
                v = media[role]
                if isinstance(v, str):
                    media_out[role] = {"mode": "image", "file": v}
                else:
                    mode = v.get("mode", "image")
                    if mode not in ("image", "video"):
                        raise ValueError(f"role {role} 仅支持 image/video 模式")
                    media_out[role] = dict(v)
            # 素材组 content：调用方提供（S 组字段多，不自动生成）
            if content and any(k.startswith(gid + ".") for k in content):
                group_content.update(
                    {k: content[k] for k in content if k.startswith(gid + ".")})
        scenes.append({"id": gid, "title": topic})

    total = sum(group_duration(catalog, gid) for gid in sel)
    if duration is not None and abs(duration - total) > 1 / 120:
        raise ValueError(
            f"declared duration {duration} 与 Σ 原生时长 {total:.2f}s 不符"
            f"（±1/120s 容差）")
    final_duration = duration if duration is not None else round(total, 3)

    return {
        "mode": "user-media",
        "language": language,
        "layout": "landscape",
        "duration": final_duration,
        "title": title,
        "presenterLabel": presenter_label,
        "scenes": scenes,
        "content": group_content,
        "basic": basic,
        "media": media_out,
        "soundEffects": sound_effects,
        "captions": captions or [],
    }
