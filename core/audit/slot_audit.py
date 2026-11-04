"""
P2 参数槽失效审计（OpenFilm 借鉴落地）

定位：OpenFilm "overrides 目标失效不静默丢弃" → .motion 模板的对应物是
copy-contract.json（内部文字槽契约）。本模块审计"契约槽是否仍可寻址"：

1. 源码引用校验 —— 每个槽的 sourceDeclaration / sourceFunction / sourceLine
   是否仍能在渲染源码（*.js）中命中；行号偏移 = moved（契约需重算行号，
   不自动删），标识消失 = missing（槽已失效，标黄提示）。
2. 参数 schema 比对 —— params.schema.json 的 copy 子树与契约槽双向 diff：
   contract_only = 契约有、schema 无（参数侧已移除 → 失效提示）；
   schema_only = schema 有、契约无（新增槽未登记契约）。

原则：只报告、不修改。失效槽不自动删，由模板作者决策（对应 OpenFilm
"overrides 目标失效不静默丢弃"）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

EXCLUDE_DIRS = {"node_modules", ".git", ".pytest_cache", "__pycache__", "_cache",
                "templates/_cache"}
CONTRACT_NAME = "copy-contract.json"
SCHEMA_NAME = "params.schema.json"

# 状态常量
FOUND = "found"            # 源码行命中 + 标识存在
MOVED = "moved"            # 标识存在但行号偏移（契约行号需重算）
MISSING = "missing"        # 标识在源码完全消失（槽失效）
CONTRACT_ONLY = "contract_only"  # 契约有、schema 无
SCHEMA_ONLY = "schema_only"      # schema 有、契约无


@dataclass
class SlotFinding:
    """单个槽的审计结论。"""
    slot_id: str
    section: str
    status: str
    evidence: List[str] = field(default_factory=list)
    meta: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "slot": self.slot_id,
            "section": self.section,
            "status": self.status,
            "evidence": self.evidence,
            "meta": self.meta,
        }


@dataclass
class SlotAuditReport:
    """槽审计报告。"""
    template_dir: str
    contract_path: Optional[str] = None
    schema_path: Optional[str] = None
    source_files: List[str] = field(default_factory=list)
    total_slots: int = 0
    findings: List[SlotFinding] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    contract_source: str = "none"       # contract-json / ecosystem-content-schema / guide-declared / none
    contract_line_anchor: bool = False  # 该来源是否支持行锚（决定能否判 moved）
    verified: bool = False              # 是否真的做了源码校验（只登记 ≠ 审过）

    @property
    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for f in self.findings:
            counts[f.status] = counts.get(f.status, 0) + 1
        return counts

    @property
    def healthy(self) -> Optional[bool]:
        """无 missing 与 contract_only 即视为健康（moved/schema_only 仅提示）。

        未真正校验（无来源 / 无源码 / 仅 param 级登记）时返回 None（未知），
        不是 True —— 没审过不等于健康。
        """
        if self.contract_source == "none" or not self.verified:
            return None
        return (self.summary.get(MISSING, 0) == 0
                and self.summary.get(CONTRACT_ONLY, 0) == 0)

    def to_dict(self) -> Dict:
        return {
            "template_dir": self.template_dir,
            "contract_source": self.contract_source,
            "contract_line_anchor": self.contract_line_anchor,
            "verified": self.verified,
            "contract_path": self.contract_path,
            "schema_path": self.schema_path,
            "source_files": self.source_files,
            "total_slots": self.total_slots,
            "summary": self.summary,
            "healthy": self.healthy,
            "findings": [f.to_dict() for f in self.findings],
            "warnings": self.warnings,
        }

    def to_markdown(self) -> str:
        lines: List[str] = []
        lines.append("## 参数槽失效审计报告")
        lines.append("")
        _KIND_LABEL = {
            "contract-json": "A 级 · 显式契约 copy-contract.json（含行锚）",
            "ecosystem-content-schema": "B 级 · 生态 content.schema.json fields[]（标识存在性，行号经实测不可靠不启用行锚）",
            "guide-declared": "C 级 · guide/schema 声明的 param 级槽（弱证据，无源码锚）",
            "none": "D 级 · 无契约来源（不做静态审计）",
        }
        lines.append(f"- 契约来源：**{_KIND_LABEL.get(self.contract_source, self.contract_source)}**")
        lines.append(f"- 模板目录：`{self.template_dir}`")
        if self.contract_path:
            lines.append(f"- 契约：`{self.contract_path}`")
        if self.schema_path:
            lines.append(f"- 参数 Schema：`{self.schema_path}`")
        lines.append(f"- 源码文件：{len(self.source_files)} 个")
        lines.append(f"- 契约槽总数：{self.total_slots}")
        lines.append("")
        lines.append("### 汇总")
        lines.append("")
        lines.append("| 状态 | 数量 | 含义 |")
        lines.append("| --- | --- | --- |")
        lines.append(f"| {FOUND} | {self.summary.get(FOUND, 0)} | 源码行命中、标识存在 |")
        lines.append(f"| {MOVED} | {self.summary.get(MOVED, 0)} | 标识存在但行号偏移（契约需重算行号） |")
        lines.append(f"| {MISSING} | {self.summary.get(MISSING, 0)} | 标识在源码消失（槽失效，标黄提示、不自动删） |")
        lines.append(f"| {CONTRACT_ONLY} | {self.summary.get(CONTRACT_ONLY, 0)} | 契约有、schema 无（参数侧移除 → 失效提示） |")
        lines.append(f"| {SCHEMA_ONLY} | {self.summary.get(SCHEMA_ONLY, 0)} | schema 有、契约无（新增槽未登记） |")
        lines.append("")
        # 按状态分组展示非健康项
        for status, title in ((MISSING, "失效槽（missing）"),
                              (CONTRACT_ONLY, "契约有而 schema 无（contract_only）"),
                              (MOVED, "行号偏移（moved）"),
                              (SCHEMA_ONLY, "schema 有而契约无（schema_only）")):
            items = [f for f in self.findings if f.status == status]
            if not items:
                continue
            lines.append(f"### {title}（{len(items)}）")
            lines.append("")
            for f in items:
                lines.append(f"- **{f.section}.{f.slot_id}**：{'; '.join(f.evidence)}")
            lines.append("")
        if self.warnings:
            lines.append("### 提示")
            lines.append("")
            for w in self.warnings:
                lines.append(f"- {w}")
            lines.append("")
        if self.healthy is True:
            lines.append("> ✅ 契约健康：无失效槽、无参数侧移除。")
        elif self.healthy is None:
            lines.append("> ⚠️ **健康状态未知**：未完成源码级校验"
                         "（无契约来源 / 无渲染源码 / 仅 param 级声明三种情形之一）。"
                         "该模板的槽位定义可能封装在受保护的运行时资产内；"
                         "请以实渲验证（omotion render / batch）确认槽位生效。")
        else:
            lines.append("> ⚠️ 发现失效 / 未登记槽，**只报告不修改**，由模板作者决策。")
        return "\n".join(lines)


def _is_excluded(p: Path) -> bool:
    parts = p.parts
    return any(ex in parts for ex in EXCLUDE_DIRS)


def find_copy_contract(root: str | Path) -> Optional[Path]:
    """递归定位 copy-contract.json（排除 node_modules/.git 等）。"""
    root = Path(root)
    for p in sorted(root.rglob(CONTRACT_NAME)):
        if _is_excluded(p):
            continue
        return p
    return None


def find_schema(root: str | Path) -> Optional[Path]:
    """递归定位 params.schema.json。"""
    root = Path(root)
    for p in sorted(root.rglob(SCHEMA_NAME)):
        if _is_excluded(p):
            continue
        return p
    return None


def find_js_sources(root: str | Path) -> List[Path]:
    """递归收集渲染源码（*.js），排除 node_modules/.git。"""
    root = Path(root)
    out = []
    for p in sorted(root.rglob("*.js")):
        if _is_excluded(p):
            continue
        out.append(p)
    return out


def load_contract_slots(contract_path: str | Path) -> List[Tuple[str, str, Dict]]:
    """解析 copy-contract.json slots → [(section, slot_id, meta)]。"""
    with open(contract_path, encoding="utf-8") as f:
        data = json.load(f)
    slots = data.get("slots") or {}
    out: List[Tuple[str, str, Dict]] = []
    for section, entries in slots.items():
        if not isinstance(entries, dict):
            continue
        for slot_id, meta in entries.items():
            out.append((section, slot_id, meta or {}))
    return out


def _source_text(sources: List[Path]) -> Dict[str, str]:
    """文件路径 → 全文文本。解码失败按 utf-8 宽容处理。"""
    texts: Dict[str, str] = {}
    for p in sources:
        try:
            texts[str(p)] = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            try:
                texts[str(p)] = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return texts


def _is_comment_or_blank(line_text: str) -> bool:
    """锚行宽容判定：注释行（// 或 /* 开头）或空行视为区块锚点。"""
    s = line_text.strip()
    return s == "" or s.startswith("//") or s.startswith("/*")


def _identifier_candidates(section: str, slot_id: str, meta: Dict) -> List[str]:
    """标识候选链（按优先级，逐个探测，取第一个命中者）。

    1. sourceDeclaration —— 字面量/变量声明名
    2. sourceFunction —— 消费函数/上下文名（E7 中部分为消费文件语义，
       如 labels 槽的 'timeline' 指 timeline.js，源码无同名标识）
    3. 槽消费路径 section.id —— 如 'labels.hero'，对应
       MOTION_COPY.labels.hero 消费点（role 契约校验核心）
    4. slot id 兜底
    """
    cands: List[str] = []
    for key in ("sourceDeclaration", "sourceFunction"):
        v = meta.get(key)
        if v:
            cands.append(v)
    cands.append(f"{section}.{slot_id}")
    # 裸 slot_id 兜底只在非严格模式启用：B 级（content.schema）的 id 是全名，
    # 若也允许裸名兜底会被同 section 的兄弟槽（如 S15.text013）误命中，造成假阳
    if not meta.get("strictIdentifier"):
        cands.append(slot_id)
    return list(dict.fromkeys(cands))


def verify_slot_in_source(section: str, slot_id: str, meta: Dict,
                          texts: Dict[str, str]) -> SlotFinding:
    """按源码证据判定槽状态（found / moved / missing）。

    证据分两层：
    1. 标识存在性 —— 沿标识候选链逐个探测（sourceDeclaration →
       sourceFunction → 槽消费路径 section.id → slot id），取第一个在
       全文出现 ≥ sourceOccurrences 的候选；全部消失 = missing（槽失效）。
    2. 行锚 —— sourceLine 行存在且行文本含命中标识 = 严格命中；
       行文本为注释/空（区块锚语义）且标识存在 = 宽容命中；
       代码行无标识或行号越界 = moved（契约行号需重算，槽仍活着）。
    """
    evidence: List[str] = []
    source_line = meta.get("sourceLine")
    occurrences = meta.get("sourceOccurrences", 1)
    need = max(1, int(occurrences or 1))

    # 若契约声明了具体源文件，优先在该文件内找（更准），未命中再回退全语料
    scoped: Dict[str, str] = texts
    src_file = meta.get("sourceFile")
    if src_file:
        matched = {k: v for k, v in texts.items()
                   if k.endswith("/" + str(src_file)) or k.endswith(str(src_file))}
        if matched:
            scoped = matched

    cands = _identifier_candidates(section, slot_id, meta)
    ident: Optional[str] = None
    total_hits = 0
    for pool in (scoped, texts) if scoped is not texts else (texts,):
        for c in cands:
            hits = sum(len(re.findall(re.escape(c), t)) for t in pool.values())
            if hits >= need:
                ident, total_hits = c, hits
                break
        if ident is not None:
            break

    if ident is None:
        detail = "、".join(f"'{c}'×{sum(len(re.findall(re.escape(c), t)) for t in texts.values())}"
                           for c in cands)
        evidence.append(f"标识候选均未命中（契约声明 ≥{occurrences}）：{detail}"
                        " → 槽已失效（标黄提示，不自动删）")
        return SlotFinding(slot_id, section, MISSING, evidence, meta)

    # 2) 行锚判定（仅当该来源支持行锚时才可能判 moved）
    if source_line and meta.get("lineAnchor", True) is not False:
        line_hit = False
        line_anchor = False
        for path, text in scoped.items():
            lines = text.splitlines()
            if not (1 <= int(source_line) <= len(lines)):
                continue
            line_text = lines[int(source_line) - 1]
            if ident in line_text:
                line_hit = True
                break
            if _is_comment_or_blank(line_text):
                line_anchor = True  # 锚行：标识存在于文件内即宽容命中
        if not line_hit and not line_anchor:
            evidence.append(f"标识 '{ident}' 仍存在（{total_hits} 次），"
                            f"但第 {source_line} 行为代码行且未含标识 → 行号已偏移")
            return SlotFinding(slot_id, section, MOVED, evidence, meta)

    scope_note = f"于 {src_file}" if src_file and scoped is not texts else "于源码"
    evidence.append(f"{scope_note}命中：标识 '{ident}'（{total_hits} 次）"
                    + (f"，第 {source_line} 行匹配" if source_line else "（无行号约束）"))
    return SlotFinding(slot_id, section, FOUND, evidence, meta)


def extract_schema_copy_slots(schema_path: str | Path) -> List[Tuple[str, str]]:
    """从 params.schema.json 提取 copy 参数槽 [(section, slot_id)]。

    支持 v2 形态：properties.copy.properties.<section>.properties.<slot_id>；
    宽容处理扁平形态：properties.copy.properties 直接为槽（section 记为 copy）。
    """
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    props = schema.get("properties") or {}
    copy = props.get("copy")
    if not isinstance(copy, dict):
        return []
    copy_props = copy.get("properties") or {}
    out: List[Tuple[str, str]] = []
    for key, sub in copy_props.items():
        if isinstance(sub, dict) and isinstance(sub.get("properties"), dict):
            # v2：key 为 section，其 properties 为槽
            for slot_id in sub["properties"]:
                out.append((key, slot_id))
        else:
            # 扁平：key 直接是槽
            out.append(("copy", key))
    return out


def compare_schema(contract_slots: List[Tuple[str, str, Dict]],
                   schema_path: Optional[str | Path]) -> Tuple[List[SlotFinding], List[str]]:
    """契约槽 vs schema copy 槽双向 diff → (findings, warnings)。"""
    findings: List[SlotFinding] = []
    warnings: List[str] = []
    if not schema_path:
        warnings.append("未找到 params.schema.json，跳过 schema 比对")
        return findings, warnings
    schema_slots = set(extract_schema_copy_slots(schema_path))
    contract_pairs = {(s, i) for s, i, _ in contract_slots}
    for s, i, meta in contract_slots:
        if (s, i) not in schema_slots:
            findings.append(SlotFinding(
                i, s, CONTRACT_ONLY,
                [f"契约登记 {s}.{i}，但 params.schema.json 的 copy 子树无此槽"
                 "（参数侧已移除 → 失效提示，不自动删）"], meta))
    for s, i in sorted(schema_slots - contract_pairs):
        findings.append(SlotFinding(
            i, s, SCHEMA_ONLY,
            [f"params.schema.json 新增 {s}.{i}，但 copy-contract.json 未登记"
             "（契约待维护）"]))
    return findings, warnings


def audit_slots(template_dir: str | Path) -> SlotAuditReport:
    """参数槽失效审计总入口。

    Args:
        template_dir: 模板实例目录（或 .motion 解包目录 / 模板核心目录）

    Returns:
        SlotAuditReport（只读审计，不修改任何文件）
    """
    root = Path(template_dir)
    if not root.is_dir():
        raise ValueError(f"模板目录不存在: {root}")

    # 多源解析：contract-json → ecosystem-content-schema → guide-declared → none
    from core.audit.contract_source import resolve_contract
    source = resolve_contract(root)

    sources = find_js_sources(root)
    schema_path = find_schema(root)
    texts = _source_text(sources) if sources else {}
    warnings: List[str] = list(source.notes)

    findings: List[SlotFinding] = []
    verified = False
    if source.kind == "contract-json" and source.slots and texts:
        # A 级：显式契约，做源码引用校验 + params.schema copy 子树双向 diff
        for section, slot_id, meta in source.slots:
            findings.append(verify_slot_in_source(section, slot_id, meta, texts))
        schema_findings, schema_warnings = compare_schema(source.slots, schema_path)
        findings.extend(schema_findings)
        warnings.extend(schema_warnings)
        verified = True
        if schema_path is None:
            warnings.append("未找到 params.schema.json，仅完成源码引用校验")
    elif source.kind == "ecosystem-content-schema" and source.slots and texts:
        # B 级：content.schema 的 fields 是「上屏内容槽」，params.schema 的 copy 是
        # 「参数树子树」，两者层级不同，不能互相 diff（否则 469 槽全被误判 contract_only）
        for section, slot_id, meta in source.slots:
            findings.append(verify_slot_in_source(section, slot_id, meta, texts))
        verified = True
        warnings.append(
            "B 级来源不做 params.schema copy 子树比对：content 槽与参数树层级不同，"
            "强对齐会制造假失效（实测会把 469 槽全判 contract_only）")
    elif source.kind == "guide-declared":
        # C 级：只有 param 级声明，没有源码锚 —— 只登记，不做源码校验
        for section, slot_id, meta in source.slots:
            findings.append(SlotFinding(slot_id, section, FOUND, [
                "param 级声明存在（guide/schema 侧），未做源码锚校验（该来源无源码锚）"
            ], meta))
        warnings.append(
            "C 级来源为 param 级声明，不含源码行锚，只核对参数是否仍被声明，"
            "不判定槽在画面上是否仍生效")
    elif source.slots and not texts:
        warnings.append("未找到渲染源码（*.js），无法做源码引用校验，仅登记槽清单")
    else:
        warnings.append(
            "无契约来源（未找到 copy-contract.json / content.schema.json 的 fields[] / "
            "guide.copy），本模板不做静态槽审计；建议以实渲验证槽位生效"
        )

    report = SlotAuditReport(
        template_dir=str(root),
        contract_source=source.kind,
        contract_line_anchor=source.line_anchor,
        contract_path=source.path if source.kind == "contract-json" else None,
        schema_path=str(schema_path) if schema_path else None,
        source_files=[str(p) for p in sources],
        total_slots=len(source.slots),
        findings=findings,
        warnings=warnings,
        verified=verified,
    )
    return report


__all__ = [
    "audit_slots", "find_copy_contract", "find_schema", "find_js_sources",
    "load_contract_slots", "verify_slot_in_source", "extract_schema_copy_slots",
    "compare_schema", "SlotAuditReport", "SlotFinding",
    "FOUND", "MOVED", "MISSING", "CONTRACT_ONLY", "SCHEMA_ONLY",
]
