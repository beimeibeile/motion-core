# -*- coding: utf-8 -*-
"""I4 智能模板选型：按内容特征（主题词/时长/画幅/口播需求）自动匹配模板。

数据源：
  1. 模板文件名约定解析：{类型}_{用途}_{画幅}_v{版本}（确定性基础属性）
  2. 内置模板属性表 TEMPLATE_PROFILES（人工维护：类型/用途/画幅/关键词/时长范围/口播/推荐场景）
     ——模板入库时同步更新该表

匹配打分（query → 排序）：
  aspect 精确匹配 +2；voiceover 一致 +2；duration 落在范围 +2（否则按距离 -1/-2）；
  topic 与模板 keywords + 类型词 命中每次 +1；总分为各项和，>0 即推荐。
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_NAME_RE = re.compile(
    r"^(?P<type>[^_]+)_(?P<use>[^_]+)_(?P<aspect>横屏|竖屏)"
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
    keywords: list[str] = field(default_factory=list)
    duration_s: tuple = (0, 300)   # (min, max)，估算
    voiceover: bool = True
    recommend: str = ""
    score: int = 0
    reasons: list[str] = field(default_factory=list)

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
                keywords=list(tp.get("keywords", [])),
                duration_s=tuple(tp.get("duration_s", (0, 300))),
                voiceover=USE_VOICEOVER.get(use, True),
                recommend=tp.get("recommend", ""),
            )
            self.profiles.append(p)

    def match(self, topic: Optional[str] = None,
              duration_s: Optional[float] = None,
              aspect: Optional[str] = None,
              voiceover: Optional[bool] = None,
              top: int = 3) -> list[TemplateProfile]:
        """按内容特征匹配模板，返回按分数降序列表（>0 才进入推荐）。"""
        ranked = []
        for p in self.profiles:
            p.match_score(topic, duration_s, aspect, voiceover)
            if p.score > 0:
                ranked.append(p)
        ranked.sort(key=lambda p: (-p.score, p.name))
        return ranked[:max(1, top)]

    def as_dicts(self) -> list[dict]:
        return [{
            "name": p.name, "type": p.type, "purpose": p.purpose,
            "aspect": p.aspect, "version": p.version,
            "keywords": p.keywords, "duration_s": list(p.duration_s),
            "voiceover": p.voiceover, "recommend": p.recommend,
        } for p in self.profiles]
