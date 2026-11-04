# -*- coding: utf-8 -*-
"""I4 智能模板选型：按内容特征（主题词/时长/画幅/口播需求）自动匹配模板。

数据源：
  1. 模板文件名约定解析：{类型}_{用途}_{画幅}_v{版本}（确定性基础属性）
  2. 内置模板属性表 TEMPLATE_PROFILES（人工维护：类型/用途/画幅/关键词/时长范围/口播/推荐场景）
     ——模板入库时同步更新该表

匹配打分（query → 排序）：
  aspect 精确匹配 +2；voiceover 一致 +2；duration 落在范围 +2（否则按距离 -1/-2）；
  topic 与模板 keywords + 类型词 命中每次 +1；总分为各项和，>0 即推荐。

可渲染性（重要）：
  本地可渲染性由"包内渲染管线"决定，而不是来源渠道：
    - 含 adapter.py（standard 形态，kinetic-user-core/1）→ adapter 管线，StandardRenderEngine
    - 含 assistant/motion.mjs（personal 分发包，motiongit-host/1）→ motion.mjs 管线，PersonalRenderEngine
    - 两者皆无 → 无法渲染（unknown），选型时默认排除
  两种管线均支持，但运行时差异大；选型结果需带 pipeline / runtime 信息，
  便于调用方判断是否走已验证路径（report.py 的 lint/参数契约也依赖同一判断）。
"""
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PIPELINE_ADAPTER = "adapter"
PIPELINE_MOTION_MJS = "motion-mjs"
PIPELINE_UNKNOWN = "unknown"

_NAME_RE = re.compile(
    r"^(?P<type>[^_]+)_(?:(?P<use>[^_]+)_)?(?P<aspect>横屏|竖屏|方屏)"
    r"(?:_v?(?P<ver>[^_]+))?$")

# 内置模板属性表（类型 → 关键词/推荐场景/时长范围/口播）
TYPE_PROFILES = {
    "多屏拼贴": {"keywords": ["拼贴", "照片墙", "多屏", "回忆", "品牌"],
                 "recommend": "多画面拼贴展示，适合品牌宣传/活动集锦/照片合集",
                 "duration_s": (10, 60), "voiceover": False},
    "活力海报": {"keywords": ["海报", "促销", "动感", "活力", "新品"],
                 "recommend": "高活力海报式画面，适合促销/新品发布/活动预告",
                 "duration_s": (10, 45), "voiceover": True},
    "科技控制台": {"keywords": ["科技", "控制台", "数据", "UI", "产品"],
                   "recommend": "科技控制台风格，适合产品发布/数据展示/发布会",
                   "duration_s": (10, 60), "voiceover": True},
    "酷炫科技风": {"keywords": ["科技", "炫酷", "数字", "粒子", "赛博"],
                   "recommend": "酷炫科技感画面，适合科技/数码/极客向内容",
                   "duration_s": (10, 60), "voiceover": True},
    "深色立体": {"keywords": ["深色", "立体", "大气", "商务", "高端"],
                 "recommend": "深色立体质感，适合商务/高端/沉稳场景",
                 "duration_s": (10, 60), "voiceover": False},
    "鲜色角色": {"keywords": ["角色", "卡通", "鲜艳", "活泼", "可爱"],
                 "recommend": "鲜艳角色画面，适合儿童/娱乐/轻快内容",
                 "duration_s": (10, 60), "voiceover": True},
    "现代图文": {"keywords": ["图文", "现代", "简约", "资讯", "知识"],
                 "recommend": "现代图文排版，适合资讯/知识/口播解说",
                 "duration_s": (15, 90), "voiceover": True},
    "纸面透视": {"keywords": ["纸面", "透视", "折纸", "文艺", "质感"],
                 "recommend": "纸面透视质感，适合文艺/故事/人文内容",
                 "duration_s": (10, 60), "voiceover": True},
}

USE_VOICEOVER = {"口播讲解": True, "叙事讲解": True,
                  "品牌宣传": False, "多屏展陈": False}


@dataclass
class TemplateProfile:
    """模板属性（含匹配所需维度）。"""
    name: str                      # 文件名（不含 .motion）
    type: str = ""
    purpose: str = ""
    aspect: str = ""               # 横屏 / 竖屏
    version: str = ""
    format: str = ""               # standard / personal-download-v1 / unknown
    pipeline: str = PIPELINE_UNKNOWN  # adapter / motion-mjs / unknown
    runtime: str = ""              # motion.json runtime.profile
    probed: bool = False           # 是否成功开包探测（False=无法判定，不据此排除）
    keywords: list[str] = field(default_factory=list)
    duration_s: tuple = (0, 300)   # (min, max)，估算
    voiceover: bool = True
    recommend: str = ""
    score: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def renderable(self) -> bool:
        """包内是否存在本地可执行的渲染管线（adapter.py 或 assistant/motion.mjs）。

        探测失败（probed=False，如占位/损坏文件）时不排除，只排除"开包成功
        且两个渲染入口都不存在"的包，避免探测受限导致误删候选项。
        """
        if not self.probed:
            return True
        return self.pipeline != PIPELINE_UNKNOWN

    @property
    def locked(self) -> bool:
        """开包确认无任何渲染入口，本地无法渲染。"""
        return not self.renderable

    def match_score(self, topic: Optional[str], duration_s: Optional[float],
                    aspect: Optional[str], voiceover: Optional[bool]) -> int:
        score, reasons = 0, []
        if aspect and aspect == self.aspect:
            score += 2
            reasons.append(f"画幅 {self.aspect} 匹配")
        if voiceover is not None and voiceover == self.voiceover:
            score += 2
            reasons.append(f"{'口播' if self.voiceover else '无口播'}匹配")
        if duration_s is not None:
            lo, hi = self.duration_s
            if lo <= duration_s <= hi:
                score += 2
                reasons.append(f"时长 {duration_s:.0f}s 在 {lo}-{hi}s 范围")
            else:
                penalty = 1 if abs(duration_s - lo) < 15 or abs(duration_s - hi) < 15 else 2
                score -= penalty
                reasons.append(f"时长 {duration_s:.0f}s 偏离范围 {lo}-{hi}s (-{penalty})")
        if topic:
            topic_l = topic.lower()
            pool = set(self.keywords) | {self.type, self.purpose}
            hits = [k for k in pool if k and (k in topic_l or topic_l in k)]
            score += len(hits)
            if hits:
                reasons.append(f"主题词命中 {len(hits)} 个: {'/'.join(hits)}")
        self.score, self.reasons = score, reasons
        return score


class TemplateIndex:
    """模板库索引 + 智能匹配器。

    Args:
        template_dir: 模板目录（扫描 *.motion 文件名）
    """

    def __init__(self, template_dir: str):
        self.template_dir = Path(template_dir)
        self.profiles: list[TemplateProfile] = []
        self._scan()

    def _scan(self) -> None:
        if not self.template_dir.exists():
            return
        for f in sorted(self.template_dir.glob("*.motion")):
            stem = f.stem
            m = _NAME_RE.match(stem)
            ttype, use, aspect, ver = (m.group("type"), m.group("use"),
                                       m.group("aspect"), m.group("ver")) \
                if m else ("", "", "", "")
            tp = TYPE_PROFILES.get(ttype, {})
            p = TemplateProfile(
                name=stem, type=ttype, purpose=use, aspect=aspect,
                version=ver or "",
                **self._probe_package(f),
                keywords=list(tp.get("keywords", [])),
                duration_s=tuple(tp.get("duration_s", (0, 300))),
                voiceover=USE_VOICEOVER.get(use, True),
                recommend=tp.get("recommend", ""),
            )
            self.profiles.append(p)

    @staticmethod
    def _probe_package(path: Path) -> dict:
        """探测包内格式 / 渲染管线 / runtime profile（单次开包，避免重复 IO）。"""
        info = {"format": "unknown", "pipeline": PIPELINE_UNKNOWN,
                "runtime": "", "probed": False}
        try:
            with zipfile.ZipFile(path, "r") as z:
                names = z.namelist()
                info["probed"] = True
                personal = "personal/motiongit.key.json" in names
                info["format"] = "personal-download-v1" if personal else "standard"
                if "adapter.py" in names:
                    info["pipeline"] = PIPELINE_ADAPTER
                elif "assistant/motion.mjs" in names:
                    info["pipeline"] = PIPELINE_MOTION_MJS
                if "motion.json" in names:
                    try:
                        motion = json.loads(z.read("motion.json").decode("utf-8"))
                        info["runtime"] = (motion.get("runtime") or {}).get("profile", "")
                    except Exception:
                        pass
        except Exception:
            pass
        return info

    @property
    def renderable_count(self) -> int:
        return sum(1 for p in self.profiles if p.renderable)

    @property
    def locked_count(self) -> int:
        return sum(1 for p in self.profiles if p.locked)

    def match(self, topic: Optional[str] = None,
              duration_s: Optional[float] = None,
              aspect: Optional[str] = None,
              voiceover: Optional[bool] = None,
              top: int = 3, renderable_only: bool = True) -> list[TemplateProfile]:
        """按内容特征匹配模板，返回按分数降序列表（>0 才进入推荐）。

        renderable_only=True（默认）只推荐可本地渲染的模板，
        避免选出 personal 加密包导致下游渲染必然失败。
        """
        ranked = []
        for p in self.profiles:
            if renderable_only and not p.renderable:
                continue
            p.match_score(topic, duration_s, aspect, voiceover)
            if p.score > 0:
                ranked.append(p)
        ranked.sort(key=lambda p: (-p.score, p.name))
        return ranked[:max(1, top)]

    def list_by_aspect(self, aspect: Optional[str] = None,
                       renderable_only: bool = False) -> list[TemplateProfile]:
        """按画幅列出可用模板（参考 adu-motion-video auto-capabilities --layout）。

        aspect 为空返回全部；传 横屏/竖屏 只返回该画幅可用模板。
        renderable_only=True 时只返回可本地渲染的模板。
        供调用方在选型前先列出"当前画幅可用规模"，避免跨画幅误选。
        """
        pool = [p for p in self.profiles if p.renderable] if renderable_only else list(self.profiles)
        if not aspect:
            return pool
        return [p for p in pool if p.aspect == aspect]

    def as_dicts(self) -> list[dict]:
        return [{
            "name": p.name, "type": p.type, "purpose": p.purpose,
            "aspect": p.aspect, "version": p.version,
            "format": p.format, "pipeline": p.pipeline, "runtime": p.runtime,
            "renderable": p.renderable,
            "keywords": p.keywords, "duration_s": list(p.duration_s),
            "voiceover": p.voiceover, "recommend": p.recommend,
        } for p in self.profiles]

    def pipeline_summary(self) -> dict:
        """按渲染管线 / 运行时汇总模板产能（供选型与报告展示真实可用规模）。"""
        summary: dict = {}
        for p in self.profiles:
            key = f"{p.pipeline}|{p.runtime or '-'}"
            summary[key] = summary.get(key, 0) + 1
        return summary
