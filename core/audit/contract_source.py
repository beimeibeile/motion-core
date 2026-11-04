"""
多源参数槽契约解析（P2 多模式兼容）

背景：`copy-contract.json` 只有自建模板才有（51 个官方模板里 0 个）。
若审计能力只认这一种来源，实际覆盖为 0。本模块按生态现实分级探测：

1. contract-json            —— 包内 copy-contract.json（强证据，含行锚）
2. ecosystem-content-schema —— 包内 content.schema.json 的 fields[]（强证据，
                               标识存在性可校验；行号经实测不可靠，不作行锚）
3. guide-declared           —— params.schema.json 的 copy 子树 / guide.json 的
                               copy[]（弱证据，param 级，无源码锚）
4. none                     —— 无可解析来源（如加密 release.bin 包）

原则：有哪级用哪级，都明确记录 provenance；推不出来就报 none，
绝不把弱证据包装成强证据。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.audit.slot_audit import (
    CONTRACT_NAME,
    find_copy_contract,
    find_js_sources,
    load_contract_slots,
)

KIND_CONTRACT = "contract-json"
KIND_CONTENT_SCHEMA = "ecosystem-content-schema"
KIND_GUIDE = "guide-declared"
KIND_NONE = "none"

CONTENT_SCHEMA_NAME = "content.schema.json"
GUIDE_NAME = "guide.json"
SCHEMA_NAME = "params.schema.json"

# 行锚支持能力（决定 verify 是否可能判 moved）
LINE_ANCHOR_KINDS = {KIND_CONTRACT}


@dataclass
class ContractSource:
    """一次契约解析的结果。"""

    kind: str = KIND_NONE
    path: Optional[str] = None
    slots: List[Tuple[str, str, Dict]] = field(default_factory=list)
    line_anchor: bool = False
    notes: List[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.slots)

    def to_dict(self) -> Dict:
        return {
            "kind": self.kind,
            "path": self.path,
            "total_slots": self.total,
            "line_anchor": self.line_anchor,
            "notes": self.notes,
        }


def _read_json(p: Path) -> Optional[Dict]:
    try:
        with open(p, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _resolve_file_line(src: str, source_texts: Dict[str, str]) -> Tuple[Optional[str], Optional[int], bool]:
    """解析 'broll.js:1310' → (源文件, 行号, 行号是否可信)。

    行号可信判定：该文件实际行数必须覆盖声明行号。实测官方 standard 包
    的 source 行号（1310）远大于 broll.js 实际行数（254），故不可信。
    """
    if not src or ":" not in src:
        return None, None, False
    fn, ln = src.rsplit(":", 1)
    if not ln.isdigit():
        return fn.strip(), None, False
    hit = next((k for k in source_texts if k.endswith("/" + fn) or k.endswith(fn)), None)
    if hit is None:
        return fn.strip(), int(ln), False
    total_lines = len(source_texts[hit].splitlines())
    return fn.strip(), int(ln), int(ln) <= total_lines


def slots_from_content_schema(schema_path: Path, root: Path) -> ContractSource:
    """把 content.schema.json 的 fields[] 映射成契约槽。

    fields 项形如：
      {"id":"A.text001","unit":"A","context":"html-text / lab",
       "source":"broll.js:1310","uses":["broll.js:1310"],
       "maxChars":4,"format":"numeric-text","required":true,"default":""}

    映射规则：
      section  = unit
      slot_id  = id 去掉 "<unit>." 前缀
      sourceDeclaration = 完整 id（实测源码以完整 id 作为键，命中 467/469）
      sourceFile / sourceLine 保留原始声明，但行号仅在可信时启用行锚
    """
    data = _read_json(schema_path)
    if not data:
        return ContractSource(KIND_NONE, notes=[f"{CONTENT_SCHEMA_NAME} 无法解析"])

    fields = data.get("fields")
    if not isinstance(fields, list) or not fields:
        return ContractSource(KIND_NONE, notes=[f"{CONTENT_SCHEMA_NAME} 无 fields[]，跳过"])

    sources = find_js_sources(root)
    texts: Dict[str, str] = {}
    for p in sources:
        try:
            texts[str(p)] = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

    notes: List[str] = []
    untrusted_lines = 0
    slots: List[Tuple[str, str, Dict]] = []
    for item in fields:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        full_id = str(item["id"])
        unit = str(item.get("unit") or full_id.split(".")[0])
        slot_id = full_id[len(unit) + 1:] if full_id.startswith(unit + ".") else full_id.split(".")[-1]
        src = item.get("source") or ""
        src_file, src_line, line_ok = _resolve_file_line(src, texts)
        uses = [u for u in (item.get("uses") or []) if isinstance(u, str)]
        if src_line is not None and not line_ok:
            untrusted_lines += 1
        meta = {
            "sourceDeclaration": full_id,
            "sourceFunction": f"{unit}.{slot_id}",
            "sourceFile": src_file,
            "sourceLine": src_line if line_ok else None,
            "declaredLine": src_line,
            "uses": uses,
            "maxChars": item.get("maxChars"),
            "format": item.get("format"),
            "required": item.get("required"),
            "default": item.get("default"),
            "lineAnchor": bool(line_ok),
            "provenance": KIND_CONTENT_SCHEMA,
            # fields 的 id 是全名（<unit>.<slot>），禁用裸 slot 名兜底以免跨 section 假阳
            "strictIdentifier": True,
        }
        slots.append((unit, slot_id, meta))

    if untrusted_lines:
        notes.append(
            f"{untrusted_lines}/{len(slots)} 个槽的 source 行号超出源文件实际行数，"
            "行号不可作行锚（已置 sourceLine=None），仅做标识存在性校验"
        )
    notes.append(f"标识候选使用完整 id（实测源码以 `<unit>.<slot>` 形式出现）")
    return ContractSource(
        kind=KIND_CONTENT_SCHEMA,
        path=str(schema_path),
        slots=slots,
        line_anchor=False,
        notes=notes,
    )


def slots_from_guide(root: Path) -> ContractSource:
    """从 params.schema.json 的 copy 子树或 guide.json 的 copy[] 解析 param 级槽。

    这是弱证据：只有参数名与填写规则，没有源码锚，不能判 moved/missing，
    只能做「参数是否仍被模板接受」的存在性核对。
    """
    from core.audit.slot_audit import extract_schema_copy_slots

    for p in sorted(root.rglob(SCHEMA_NAME)):
        if "node_modules" in p.parts:
            continue
        pairs = extract_schema_copy_slots(p)
        if pairs:
            slots = [(s, i, {"sourceDeclaration": None, "provenance": KIND_GUIDE,
                             "lineAnchor": False}) for s, i in pairs]
            return ContractSource(KIND_GUIDE, str(p), slots, False,
                                  [f"来自 {SCHEMA_NAME} 的 copy 子树（param 级，无源码锚）"])

    for p in sorted(root.rglob(GUIDE_NAME)):
        if "node_modules" in p.parts:
            continue
        data = _read_json(p)
        if not data:
            continue
        copy = data.get("copy")
        if not isinstance(copy, list) or not copy:
            continue
        slots = []
        for item in copy:
            if not isinstance(item, dict) or not item.get("param"):
                continue
            param = str(item["param"])
            slots.append(("guide", param, {
                "sourceDeclaration": None,
                "provenance": KIND_GUIDE,
                "lineAnchor": False,
                "label": item.get("label"),
                "from": item.get("from"),
                "rule": item.get("rule"),
            }))
        if slots:
            return ContractSource(KIND_GUIDE, str(p), slots, False,
                                  ["来自 guide.json 的 copy[]（param 级声明，无源码锚）"])

    return ContractSource(KIND_NONE, notes=["未找到可解析的 copy 槽来源"])


def resolve_contract(root: str | Path) -> ContractSource:
    """按优先级解析契约来源：contract → content-schema → guide → none。"""
    root = Path(root)

    contract_path = find_copy_contract(root)
    if contract_path is not None:
        slots = load_contract_slots(contract_path)
        if slots:
            return ContractSource(
                kind=KIND_CONTRACT,
                path=str(contract_path),
                slots=slots,
                line_anchor=True,
                notes=[f"来自显式契约 {CONTRACT_NAME}（含行锚与出现次数声明）"],
            )

    for p in sorted(root.rglob(CONTENT_SCHEMA_NAME)):
        if "node_modules" in p.parts:
            continue
        src = slots_from_content_schema(p, root)
        if src.slots:
            return src

    return slots_from_guide(root)


__all__ = [
    "ContractSource",
    "resolve_contract",
    "slots_from_content_schema",
    "slots_from_guide",
    "KIND_CONTRACT", "KIND_CONTENT_SCHEMA", "KIND_GUIDE", "KIND_NONE",
    "LINE_ANCHOR_KINDS",
]
