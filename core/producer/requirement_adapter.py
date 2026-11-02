# -*- coding: utf-8 -*-
"""模板需求智能处理层（任务 #52 / I8 批量模式前置）。

参考 adu-motion-video v3.14.1-rc.1 ①全库自动选型后半（auto-plan/auto-build）的
核心原则——"不让用户手填各套字段"：用户只需给**整篇台词 + 素材**，系统自动完成
  台词拆解 → 语义阶段 → 场景映射（纯文本 B 组 / 素材组判定）→ 素材槽位判定
  → 契约参数生成 → 缺项阻塞报告。

机制要点（理解原理后自主实现，非复制）：
  1. needs-binding / blocked 必须补 brief 或调整分镜并重新规划，不硬跑
  2. 缺真实素材、超容量、错误数量不能绕过
  3. 提供原文 contentEvidence 与每段 ownershipRefs，覆盖所有保留台词且不重复不错序
  4. 构建会重验选型和素材（build 时复查 media 绑定）

落点：批量任务理解期（任务规范 §3.2）+ 即时任务理解（任务理解协议 §二），
供 producer / 应用层在填参数前调用；未通过 blockers 的任务不得进入渲染。

典型流程：
    from core.producer.requirement_adapter import RequirementAdapter
    ra = RequirementAdapter(catalog_path)
    plan = ra.plan(script="整篇台词…", layout="landscape",
                   assets={"thumbnail": "素材/a.jpg"})   # 可无素材
    if plan.blockers:
        print(plan.blockers_report())                    # 缺项 → 回理解期
        return
    result = ra.build(plan, media=assets)                # 契约合法参数
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from core.param_manager.standard_adapter import (
    BASIC_LIMITS,
    BASIC_REQUIREMENTS,
    LAYOUTS,
    build_standard_params,
    group_duration,
    group_media_roles,
)

# 纯文本 B 组（无 media 依赖）→ 语义阶段自动映射
TEXT_ROUTE_GROUPS = ("B1", "B2", "B3", "B4")


@dataclass
class SemanticStage:
    """台词拆解后的一个语义阶段（对应一个场景组）。"""
    index: int                 # 0-based 序号
    title: str                 # 短标题（≤ 模板 heading/keyword 容量）
    text: str                  # 原文（该阶段保留的台词，不重复不错序）
    cues: list[str] = field(default_factory=list)  # 保留的数字/否定/条件/单位等关键 cue


@dataclass
class RequirementPlan:
    """需求→场景映射结果（含缺项判定，未通过不得进渲染）。"""
    script: str
    layout: str
    voiceover: bool
    stages: list[SemanticStage] = field(default_factory=list)
    route: str = ""            # "text"（B 组纯文本）/ "media"（素材组）
    selected: list[str] = field(default_factory=list)   # 选中组 id
    media_required: list[str] = field(default_factory=list)  # 模板要求的 media roles
    media_provided: dict = field(default_factory=dict)       # 用户已提供
    blockers: list[str] = field(default_factory=list)        # 阻塞原因（非空 = 不得渲染）
    warnings: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.blockers)

    def blockers_report(self) -> str:
        lines = ["需求智能处理未通过，回任务理解期补齐："]
        lines += [f"  - {b}" for b in self.blockers]
        if self.warnings:
            lines.append("警告（不阻塞，需人工复核）：")
            lines += [f"  - {w}" for w in self.warnings]
        return "\n".join(lines)


@dataclass
class BuildResult:
    """契约参数生成结果。blocked=True 时 params 为 None。"""
    plan: RequirementPlan
    params: Optional[dict] = None
    content_evidence: list[str] = field(default_factory=list)   # 保留台词原文（逐段）
    ownership_refs: dict = field(default_factory=dict)          # 组 → 原文来源标记
    blocked: bool = False
    error: str = ""


# ---------- 台词拆解 ----------

_SENT_SPLIT = re.compile(r"[。！？；\n]+")


def split_sentences(text: str) -> list[str]:
    """按中文句读标点切句，去空。"""
    return [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]


_CUE_RE = re.compile(
    r"(\d+(?:\.\d+)?%?|百分之[一二三四五六七八九十百千]+"
    r"|\b[A-Za-z]+\d+\b|不|没|否|非|必须|禁止|除非|如果|至少|最多)")


def _extract_cues(sentence: str) -> list[str]:
    return list(dict.fromkeys(_CUE_RE.findall(sentence)))


def _short_title(sentences: list[str], cap: int = 10) -> str:
    """取首句关键词作为短标题（≤ cap 字，超限截断）。"""
    first = sentences[0] if sentences else ""
    # 去掉句末标点与"的/是/了"等虚词，保留实义
    head = re.sub(r"[。！？；：,.!?:;]$", "", first).strip()
    head = re.sub(r"^(关于|围绕|让我们|今天要|给大家|接下来|首先|最后)", "", head)
    if not head:
        head = first[:cap]
    # 按字符截断（中英文混合时 len 与显示宽度近似；容量以字符计）
    return head[:cap] or "主题"


def analyze_script(script: str, max_stages: int = 4) -> list[SemanticStage]:
    """整篇台词 → 语义阶段（短标题 + 原文 + 关键 cue）。

    保留限定词/数字/否定/条件/单位等关键 cue，覆盖所有保留台词且不重复不错序。
    当阶段数超过 max_stages 时，相邻句合并（按 B 组上限 4 段）。
    """
    sentences = split_sentences(script)
    if not sentences:
        return []
    if len(sentences) <= max_stages:
        groups = [[s] for s in sentences]
    else:
        # 均分为 max_stages 段（保持原序）
        base = len(sentences) // max_stages
        extra = len(sentences) % max_stages
        groups, i = [], 0
        for k in range(max_stages):
            n = base + (1 if k < extra else 0)
            groups.append(sentences[i:i + n])
            i += n
    stages = []
    for i, grp in enumerate(groups):
        text = "".join(grp)
        cues = []
        for s in grp:
            cues.extend(_extract_cues(s))
        stages.append(SemanticStage(
            index=i, title=_short_title(grp), text=text,
            cues=list(dict.fromkeys(cues))))
    return stages


# ---------- 素材槽位判定 ----------

_EXT_ROLES = {
    ".mp4": "presenter", ".mov": "presenter", ".webm": "presenter",
    ".jpg": "footage", ".jpeg": "footage", ".png": "footage",
    ".webp": "footage", ".gif": "footage",
    ".mp3": "voice", ".wav": "voice", ".m4a": "voice", ".aac": "voice",
    ".srt": "captions", ".json": "captions",
}


def _infer_role(path: str) -> str:
    from pathlib import Path
    return _EXT_ROLES.get(Path(str(path)).suffix.lower(), "footage")


def classify_assets(assets: dict) -> dict:
    """把用户提供的素材归类为 spec 标准角色（presenter/footage/voice/captions）。

    assets 形如 {"role_or_name": "/path/..."}。
    - 已按 spec 角色命名（presenter/footage/voice/captions/music）→ 保留
    - 未按角色命名 → 键名直接作为模板槽位候选（模板 media role 如 wall/thumbnail
      可能是任意名字），同时按扩展名推断 spec 角色补充（供槽位匹配兜底）
    """
    roles: dict = {}
    spec = ("presenter", "footage", "voice", "captions", "music")
    for k, v in (assets or {}).items():
        role = k.split(".", 1)[0] if "." in k else k
        roles.setdefault(role, v)              # 键名直配（模板槽位名或 spec 角色）
        if role not in spec:
            roles.setdefault(_infer_role(v), v)  # 扩展名推断兜底
    return roles


# ---------- 需求适配器 ----------

class RequirementAdapter:
    """模板需求智能处理：台词/素材 → 契约参数（或阻塞报告）。

    Args:
        catalog_path: 模板 core/catalog.json 绝对路径
                      （standard 模板解包后目录；zip 由调用方先解包）
    """

    def __init__(self, catalog_path: str):
        self.catalog_path = catalog_path
        # 模板 catalog 中全部组（id → 是否纯文本）
        import json
        with open(catalog_path, encoding="utf-8") as f:
            self._catalog = json.load(f)
        self._text_only = {
            g["id"] for g in self._catalog.get("groups", [])
            if not (g.get("media") or [])
        }
        self._all_ids = [g["id"] for g in self._catalog.get("groups", [])]

    # ---- 场景映射 ----

    def _text_route(self, stages: list[SemanticStage],
                    voiceover: bool) -> tuple[list[str], list[str], list[str]]:
        """纯文本路线：按阶段数选 B 组前缀，返回 (selected, blockers, warnings)。

        规则：阶段数 ≤4 → B1..B_{n}；素材组缺 media → needs-binding 阻塞。
        """
        n = len(stages)
        if n == 0:
            return [], ["台词为空，无法映射场景（回任务理解期补齐台词）"], []
        if n > len(TEXT_ROUTE_GROUPS):
            return [], [
                f"语义阶段数 {n} 超过 B 组上限 {len(TEXT_ROUTE_GROUPS)}，"
                f"请精简台词或提供素材走素材组路线"], []
        selected = list(TEXT_ROUTE_GROUPS[:n])
        # voiceover 与 B 组纯文本路线一致（无 presenter/voice 素材，走字幕+音效）
        warnings = []
        if voiceover and not stages[0].cues:
            warnings.append("口播需求已声明，但台词未含可配音的完整句读，"
                            "渲染后请抽帧复核字幕/音效是否符合交付目的")
        return selected, [], warnings

    def _media_route(self, stages: list[SemanticStage],
                     assets: dict, content: Optional[dict] = None
                     ) -> tuple[list[str], list[str], list[str]]:
        """素材路线：在模板 media 组中选与台词阶段数匹配的组。

        规则（第一版，务实）：
          - 候选 = catalog 中带 media 的组，且其 media roles ⊆ 用户素材角色
          - 按阶段数取前 n 个候选（保持 catalog 原序）
          - 缺素材角色 → needs-binding 阻塞；候选不足 → 阻塞（台词不可完整覆盖）
          - 素材组 required content 字段：未提供 → needs-binding 阻塞
            （S 组字段多、容量紧，属强交互点；不自动硬造占位）
        """
        roles = classify_assets(assets)
        content = content or {}
        blockers, warnings = [], []
        candidates = []
        for g in self._catalog.get("groups", []):
            mroles = list(g.get("media", []) or [])
            if not mroles:
                continue
            if all(r in roles for r in mroles):
                candidates.append(g["id"])
        n = len(stages)
        if not candidates:
            blockers.append(
                "模板中无 media 组可覆盖当前素材角色 "
                f"（已提供: {sorted(roles)}；请补素材或改纯文本路线）")
            return [], blockers, warnings
        if n > len(candidates):
            # 阿杜机制：覆盖所有保留台词且不重复不错序；候选不足 → 阻塞而非丢弃
            blockers.append(
                f"语义阶段数 {n} 超过可用素材组 {len(candidates)}"
                f"（候选: {candidates}），台词无法完整覆盖；"
                f"请精简台词或补充可覆盖更多素材组的素材")
            return [], blockers, warnings
        selected = candidates[:n]
        # 复查绑定：选中组的 media roles 必须全部已提供
        for gid in selected:
            for role in group_media_roles(self._catalog, gid):
                if role not in roles:
                    blockers.append(f"组 {gid} 需绑定素材 role '{role}'（needs-binding）")
        # 素材组内容完整性：required content 字段必须覆盖（缺内容 → needs-binding）
        for gid in selected:
            req = self._required_content_fields(gid)
            if not req:
                continue
            missing = [f for f in req if not (content.get(f) or "").strip()]
            if missing:
                blockers.append(
                    f"组 {gid} 需 {len(req)} 个必填文本字段，"
                    f"缺 {len(missing)} 个（如 {missing[0]}），"
                    f"请通过 content 参数提供（缺内容 needs-binding，"
                    f"受模板 maxChars 容量约束）")
        return selected, blockers, warnings

    def _required_content_fields(self, group_id: str) -> list[str]:
        """读取模板 content.schema.json，返回该组 required 文本字段 id 列表。"""
        from pathlib import Path
        schema_path = Path(self.catalog_path).parent / "content.schema.json"
        if not schema_path.exists():
            return []
        import json
        with open(schema_path, encoding="utf-8") as f:
            schema = json.load(f)
        fields = schema.get("fields", []) if isinstance(schema, dict) else []
        return [f["id"] for f in fields
                if f.get("id", "").startswith(group_id + ".") and f.get("required")]

    # ---- 主入口 ----

    def plan(self, script: str, layout: str = "landscape",
             voiceover: bool = True, assets: Optional[dict] = None,
             content: Optional[dict] = None,
             max_stages: int = 4) -> RequirementPlan:
        """需求理解：台词拆解 → 场景映射 → 素材槽位判定 → 阻塞报告。

        不让用户手填各套字段——仅需台词（必填）+ 素材（可选）+ 素材组内容（可选）。
        """
        if layout not in LAYOUTS:
            raise ValueError(f"layout 仅支持 {'/'.join(LAYOUTS)}")
        stages = analyze_script(script, max_stages=max_stages)
        assets = dict(assets or {})
        content = dict(content or {})
        roles = classify_assets(assets)
        has_media_assets = bool(roles)

        plan = RequirementPlan(
            script=script, layout=layout, voiceover=voiceover,
            stages=stages, media_provided=roles)

        if has_media_assets:
            plan.route = "media"
            sel, blockers, warnings = self._media_route(stages, assets, content)
        else:
            plan.route = "text"
            sel, blockers, warnings = self._text_route(stages, voiceover)

        plan.selected = sel
        plan.media_required = []
        for gid in sel:
            plan.media_required.extend(group_media_roles(self._catalog, gid))
        plan.blockers = blockers
        plan.warnings = warnings
        return plan

    # ---- 语义阶段 → B 组字段（真实台词映射，替代 topic 示例） ----

    def _stage_basic(self, plan: RequirementPlan,
                     language: str = "zh") -> dict:
        """把拆解后的语义阶段映射到选中 B 组 basic 字段。

        容量规则：字段文本不超过 BASIC_LIMITS（zh；en=zh*3+12）。
        超限字段截断，并记录到 plan.warnings（原文仍在 contentEvidence，不丢失）。
        点数数组：B2.points 最多 2 项（模板 fitBody 限制 one/two explicit lines）。
        """
        mapping = {
            "B1": ("keyword", "explanation"),
            "B2": ("heading", "points"),
            "B3": ("heading", "left", "right", "conclusion"),
            "B4": ("summary", "action"),
        }
        out: dict = {}
        for gid, stage in zip(plan.selected, plan.stages):
            fields = mapping.get(gid)
            if not fields:
                continue
            # 阶段文本：去掉句末标点，便于填入字段
            clean = re.sub(r"[。！？；：,.!?:;]+$", "", stage.text).strip()
            if gid == "B1":
                out["keyword"] = self._clip(stage.title, "keyword", language, plan)
                out["explanation"] = self._clip(clean, "explanation", language, plan)
            elif gid == "B2":
                out["heading"] = self._clip(stage.title, "heading", language, plan)
                # points：阶段内分句（或整句拆分），取前 2 条
                pts = [s for s in split_sentences(stage.text) if s.strip()][:2]
                if len(pts) < 2:
                    pts = [clean] if clean else [stage.title]
                out["points"] = [
                    self._clip(p, "points", language, plan, is_point=True)
                    for p in pts
                ]
            elif gid == "B3":
                out["heading"] = self._clip(stage.title, "heading", language, plan)
                # left/right：优先阶段内前两个分句；单句时按中点切分为两侧
                # （纯切分不编造，B3 表达 A/B 对比结构）
                sides = [s for s in split_sentences(stage.text) if s.strip()]
                if len(sides) >= 2:
                    l, r = sides[0], sides[1]
                elif sides:
                    half = len(sides[0]) // 2
                    l, r = sides[0][:half], sides[0][half:]
                else:
                    l = r = stage.title
                out["left"] = self._clip(l, "left", language, plan)
                out["right"] = self._clip(r, "right", language, plan)
                out["conclusion"] = self._clip(clean, "conclusion", language, plan)
            elif gid == "B4":
                out["summary"] = self._clip(stage.title, "summary", language, plan)
                out["action"] = self._clip(clean, "action", language, plan)
        return out

    def _clip(self, text: str, key: str, language: str,
              plan: RequirementPlan, is_point: bool = False) -> str:
        """按模板原生容量截断文本；超限记 warning（原文在 contentEvidence）。"""
        limit = BASIC_LIMITS.get(key)
        if is_point:
            # B2.points 走模板 fitBody 单行校验（非字符硬上限）；取近似容量
            cap = 24
        elif limit is None:
            return text
        else:
            cap = limit if language == "zh" else limit * 3 + 12
        if len(text) > cap:
            plan.warnings.append(
                f"字段 {key} 台词原文 {len(text)} 字超出模板容量 {cap} 字，"
                f"已截断（原文保留在 contentEvidence）")
            return text[:cap]
        return text

    # ---- 契约参数生成 ----

    def build(self, plan: RequirementPlan, title: Optional[str] = None,
              topic: Optional[str] = None, language: str = "zh",
              content: Optional[dict] = None,
              media: Optional[dict] = None,
              captions: Optional[list] = None,
              presenter_label: str = "",
              sound_effects: str = "native",
              duration: Optional[float] = None) -> BuildResult:
        """契约参数生成（复用 standard_adapter.build_standard_params）。

        未通过 blockers 的任务不得生成参数（blocked=True）。
        contentEvidence：逐阶段保留台词原文；ownershipRefs：组 → 阶段来源。
        """
        if plan.blocked:
            return BuildResult(plan=plan, blocked=True,
                               error="需求未通过（blockers 非空），不得进渲染")

        # 素材组内容完整性独立重验（防绕过 plan 直接 build → S 组文本为空）
        for gid in plan.selected:
            req = self._required_content_fields(gid)
            if not req:
                continue
            provided = content or {}
            missing = [f for f in req if not (provided.get(f) or "").strip()]
            if missing:
                return BuildResult(
                    plan=plan, blocked=True,
                    error=f"组 {gid} 缺 {len(missing)} 个必填文本字段"
                          f"（如 {missing[0]}）；缺内容 needs-binding，不得进渲染")

        title = title or (plan.stages[0].title if plan.stages else "示例")
        topic = topic or title
        try:
            params = build_standard_params(
                self.catalog_path,
                selected=plan.selected,
                title=title,
                topic=topic,
                language=language,
                layout=plan.layout,
                duration=duration,
                presenter_label=presenter_label,
                content=content,
                media=media or plan.media_provided,
                captions=captions,
                sound_effects=sound_effects,
            )
        except ValueError as e:
            return BuildResult(plan=plan, blocked=True, error=str(e))

        # 覆盖 basic：以台词拆解的语义阶段内容为准，而非 topic 示例文本
        stage_basic = self._stage_basic(plan, language)
        if stage_basic:
            params["basic"].update(stage_basic)

        evidence = [s.text for s in plan.stages if s.text]
        ownership = {gid: s.title for gid, s in
                     zip(plan.selected, plan.stages)}
        return BuildResult(
            plan=plan, params=params, blocked=False,
            content_evidence=evidence, ownership_refs=ownership)
