"""模板审计模块 —— P2 参数槽失效审计（copy-contract 维护 / role 契约）。"""

from core.audit.slot_audit import (
    audit_slots,
    find_copy_contract,
    find_schema,
    find_js_sources,
    load_contract_slots,
    verify_slot_in_source,
    extract_schema_copy_slots,
    compare_schema,
    SlotAuditReport,
    SlotFinding,
    FOUND,
    MOVED,
    MISSING,
    CONTRACT_ONLY,
    SCHEMA_ONLY,
)

__all__ = [
    "audit_slots",
    "find_copy_contract",
    "find_schema",
    "find_js_sources",
    "load_contract_slots",
    "verify_slot_in_source",
    "extract_schema_copy_slots",
    "compare_schema",
    "SlotAuditReport",
    "SlotFinding",
    "FOUND",
    "MOVED",
    "MISSING",
    "CONTRACT_ONLY",
    "SCHEMA_ONLY",
]
