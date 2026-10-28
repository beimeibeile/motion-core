"""Parameter Manager - JSON Schema parsing and preset management."""
from .schema import ParamSchema
from .presets import PresetManager
from .auto import build_default_params, build_default_scenes
from .standard_adapter import (
    BASIC_REQUIREMENTS,
    TEXT_ONLY_IDS,
    basic_for_group,
    build_standard_params,
    group_duration,
)

__all__ = [
    "ParamSchema",
    "PresetManager",
    "build_default_params",
    "build_default_scenes",
    "build_standard_params",
    "basic_for_group",
    "group_duration",
    "BASIC_REQUIREMENTS",
    "TEXT_ONLY_IDS",
]
