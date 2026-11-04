# -*- coding: utf-8 -*-
"""模板 runtime 能力探测（OpenFilm P0-a 借鉴）。

"runtime 能力表"：静态分析模板实例源码，探测其逐帧直连能力 ——
是否有 renderAt/renderFrame 入口、入参单位（秒/帧）、是否支持
?preview / ?t=、canvas 还是 DOM 型、时钟风险（复用 clock_freeze）、
音频依赖等。

用途：
  - 编辑器"参数化调试闭环"：探测到 renderAt 直连入口的模板，时间线
    拖动 → postMessage({t}) → iframe 内 renderAt(t)，单帧毫秒级；
  - 质量门 / inspect 输出能力表，作为 .motion 规范的一部分。
"""

import re
from pathlib import Path

from ..renderer.clock_freeze import collect_sources

# 逐帧渲染入口候选（函数定义 / 赋值 / window 暴露）
_RENDER_ENTRY_RE = re.compile(
    r"(?:function\s+)?"
    r"(?P<name>render(?:At|Frame))"
    r"\s*(?:\(|\s*=\s*(?:function|\()|:\s*function|\s*=\s*\([^)]*\)\s*=>)"
)
_RENDER_ENTRY_BODY_RES = (
    # function renderAt(t) { ... }
    re.compile(
        r"(?:function\s+)?render(?:At|Frame)\s*\([^)]*\)\s*\{(?P<body>.*?)\}",
        re.DOTALL,
    ),
    # window.renderAt = function (t) { ... }（standard 模板形态）
    re.compile(
        r"(?:window\.)?render(?:At|Frame)\s*=\s*function\s*\([^)]*\)\s*\{(?P<body>.*?)\}",
        re.DOTALL,
    ),
)

# URL 参数探测：URLSearchParams + .get('t') / .get('preview')（变量名任意）
_T_PARAM_RES = (
    re.compile(r"\.get\(\s*['\"]t['\"]\s*\)"),
    re.compile(r"searchParams\.get\(\s*['\"]t['\"]\s*\)"),
    re.compile(r"location\.search[^;]{0,40}\bt\b"),
)
_PREVIEW_PARAM_RES = (
    re.compile(r"\.get\(\s*['\"]preview['\"]\s*\)"),
    re.compile(r"searchParams\.get\(\s*['\"]preview['\"]\s*\)"),
    re.compile(r"location\.(?:search|hash)[^;]{0,40}preview"),
)

# 参数单位启发式：先查声明级（注释/场景秒边界），再查显式毫秒换算（seconds），最后帧号换算（frames）
_SECONDS_HINTS = (
    r"t\s*\*\s*1000",
    r"t\s*/\s*1000",
    r"t\s*\*\s*frameDuration",
    r"sc\.(?:s|e)\b",  # 场景秒区间（standard 体系 t >= sc.s && t < sc.e）
)
_FRAMES_HINTS = (
    r"Math\.round\(\s*t\s*\*\s*(?:fps|\d+)",  # renderAt(t): frame = round(t*fps)
    r"t\s*\*\s*fps",
    r"t\s*/\s*fps",
    r"frame\s*[/\*]\s*\d+",  # renderFrame(frame): frame / fps 或 frame * 步长
    r"frame\s*=\s*t",
)


def _has_url_param(source: str, patterns: tuple) -> bool:
    # 先确认用了 URLSearchParams（避免 .get('t') 误伤其他对象），
    # 再匹配任意变量名上的 .get('t') / .get('preview')
    if "URLSearchParams" not in source:
        return False
    return any(p.search(source) for p in patterns)


def _find_render_entry(source: str) -> tuple[str | None, str | None, str | None]:
    """返回 (入口名, 函数体, 声明上下文)；未发现返回 (None, None, None)。"""
    m = _RENDER_ENTRY_RE.search(source)
    if not m:
        return None, None, None
    name = m.group("name")
    body = None
    # 函数体提取：function renderAt(t){...} 与 window.renderAt = function (t){...} 两种形态
    for body_re in _RENDER_ENTRY_BODY_RES:
        bm = body_re.search(source)
        if bm:
            body = bm.group("body")
            break
    start = max(0, m.start() - 160)
    end = min(len(source), m.end() + 160)
    context = source[start:end]
    return name, body, context


def _infer_param_unit(entry_body: str | None, entry_context: str | None = None) -> str:
    # 1) 声明级注释（最权威）：入口上下文含 "seconds"/"秒" → 秒
    if entry_context and re.search(r"seconds|秒", entry_context):
        return "seconds"
    if not entry_body:
        return "unknown"
    # 2) 实现级换算启发式：显式毫秒换算 → seconds
    for hint in _SECONDS_HINTS:
        if re.search(hint, entry_body):
            return "seconds"
    # 3) 帧号换算 → frames
    for hint in _FRAMES_HINTS:
        if re.search(hint, entry_body):
            return "frames"
    return "unknown"


def _infer_kind(source: str) -> str:
    if "<canvas" in source and ("getContext(" in source or "canvas" in source.lower()):
        return "canvas"
    if re.search(r"getElementById|querySelector(?:All)?\s*\(", source):
        return "dom"
    return "unknown"


def probe_runtime(template_dir: str | Path) -> dict:
    """静态探测模板实例的 runtime 能力表。

    返回字段：
      kind / clock_risk / render_entry / param_unit / supports_t /
      supports_preview / audio_deps / entry_html / fps
    （fps 从模板 motion.json 读取，缺失为 None）
    """
    root = Path(template_dir)
    result: dict = {
        "kind": "unknown",
        "clock_risk": False,
        "render_entry": None,
        "param_unit": "unknown",
        "supports_t": False,
        "supports_preview": False,
        "audio_deps": False,
        "entry_html": None,
        "fps": None,
    }
    if not root.is_dir():
        return result

    sources = collect_sources(root)
    if not sources:
        return result

    result["kind"] = _infer_kind(sources)
    result["clock_risk"] = any(
        m in sources for m in ("Date.now", "new Date(", "performance.now",
                               "requestAnimationFrame", "Math.random")
    )
    result["supports_t"] = _has_url_param(sources, _T_PARAM_RES)
    result["supports_preview"] = _has_url_param(sources, _PREVIEW_PARAM_RES)
    result["audio_deps"] = bool(
        re.search(r"\.(?:mp3|wav|m4a|aac|ogg)(['\"]|\)|`)|<audio|<video", sources)
    )

    entry_name, entry_body, entry_context = _find_render_entry(sources)
    if entry_name:
        result["render_entry"] = entry_name
        result["param_unit"] = _infer_param_unit(entry_body, entry_context)

    # 入口 HTML（能力表定位字段）
    from ..renderer.clock_freeze import find_entry_html
    entries = find_entry_html(root)
    if entries:
        result["entry_html"] = entries[0].relative_to(root).as_posix()

    # fps：模板声明（motion.json / config 常见字段）
    for name in ("motion.json", "config.json", "config.js"):
        p = root / name
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        m = re.search(r"['\"]fps['\"]\s*:\s*(\d+)", text)
        if m:
            result["fps"] = int(m.group(1))
            break
    return result
