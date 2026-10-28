"""Parameter Manager - JSON Schema parsing and preset management."""
from .schema import ParamSchema
from .presets import PresetManager
from .auto import build_default_params, build_default_scenes
from .standard_adapter import (
    BASIC_LIMITS,
    BASIC_REQUIREMENTS,
    TEXT_ONLY_IDS,
    basic_for_group,
    build_standard_params,
    group_duration,
    group_media_roles,
    validate_basic_text,
)

__all__ = [
    "ParamSchema",
    "PresetManager",
    "build_default_params",
    "build_default_scenes",
    "build_standard_params",
    "basic_for_group",
    "group_duration",
    "group_media_roles",
    "validate_basic_text",
    "BASIC_LIMITS",
    "BASIC_REQUIREMENTS",
    "TEXT_ONLY_IDS",
]
