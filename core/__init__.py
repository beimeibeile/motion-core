"""
O.motion Core - .motion format shared core library.

Modules:
    template_parser: Parse standard and personal-download-v1 .motion templates
    renderer: HTML Canvas frame rendering + FFmpeg encoding
    quality_gate: Four-layer quality inspection (static/params/render/output)
    param_manager: JSON Schema parameter parsing and preset management
    fragment_manager: Reusable animation fragment library and composition
    utils: Configuration, logging, filesystem utilities
"""

__version__ = "0.1.0"
__all__ = [
    "template_parser",
    "renderer",
    "quality_gate",
    "param_manager",
    "fragment_manager",
    "utils",
]
