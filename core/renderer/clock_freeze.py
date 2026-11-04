# -*- coding: utf-8 -*-
"""Clock freeze prelude (OpenFilm FILM_CLOCK 借鉴, P0-b).

对 "DOM + 动画库" 型模板注入时钟冻结 prelude：把 performance.now / Date /
Math.random / requestAnimationFrame 时间戳全部钉到渲染时间 t，使"预览"
与"成片"逐帧一致，消除模板读墙钟导致的确定性偏差（预览对了、导出错了）。

注入策略：
  - 仅对命中时钟/随机风险的模板入口 HTML 启用（has_clock_risk）；
    单 canvas 纯函数模板（renderAt 不读墙钟）自动跳过。
  - 注入幂等：重复注入不产生重复 prelude。
  - 只改时钟源，不影响纯函数 renderAt 的输入输出，canvas 型误注入也无副作用。

用法：
  from .clock_freeze import find_entry_html, has_clock_risk, inject_clock_freeze
  for html in find_entry_html(project_dir):
      if has_clock_risk(html.read_text(...)):
          html.write_text(inject_clock_freeze(html.read_text(...)), ...)
"""

import re
from pathlib import Path

# FILM_CLOCK 等价 prelude（浏览器侧，模板脚本之前执行）：
#   ① performance.now()/rAF 回调时间戳 → t*1000（毫秒）
#   ② Date.now() → 固定纪元 + t*1000
#   ③ Math.random() → 用 t 播种的 mulberry32（确定性伪随机）
#   ④ __omotionSettle(t) → Web Animations / CSS 动画 currentTime=t*1000 并暂停
#   ⑤ 媒体 currentTime setter +0.001，防帧边界取错帧
#   ⑥ 调用方截图前需等两层 rAF（提交布局 + 图层栅格化），见 render 侧约定
CLOCK_FREEZE_JS = r"""// omotion clock-freeze prelude (OpenFilm FILM_CLOCK 借鉴, P0-b)
(function () {
  if (window.__omotionClockFrozen) return;
  var _t = 0;
  var _epoch = Date.now();
  var _realPerf = (typeof performance !== 'undefined' && performance.now)
    ? performance.now.bind(performance) : null;

  // mulberry32：确定性伪随机（用 t 播种，同一 t 永远同一序列）
  function _seed(seed) {
    var s = seed >>> 0;
    return function () {
      s = (s + 0x6D2B79F5) | 0;
      var t = Math.imul(s ^ (s >>> 15), 1 | s);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  var _rnd = _seed(0x9e3779b9);

  function _freeze(t) {
    _t = t;
    // ③ 每次换 t 重新播种，保证同 t 同随机序列
    _rnd = _seed(0x9e3779b9 ^ Math.floor(t * 1000));
  }

  // ① performance.now() → t*1000
  if (_realPerf) { performance.now = function () { return _t * 1000; }; }
  // ② Date 固定纪元 + t
  Date.now = function () { return _epoch + _t * 1000; };
  // ③ Math.random → 确定性序列
  Math.random = function () { return _rnd(); };
  // ④ rAF 回调时间戳 → t*1000（rAF 本身仍走真实调度，仅时间戳冻结）
  if (typeof requestAnimationFrame === 'function') {
    var _raf = requestAnimationFrame;
    window.requestAnimationFrame = function (cb) {
      return _raf(function (ts) { cb(_t * 1000); });
    };
  }
  // ⑤ 媒体 currentTime setter +0.001 防帧边界取错帧
  try {
    var _descr = Object.getOwnPropertyDescriptor(
      HTMLMediaElement.prototype, 'currentTime');
    if (_descr && _descr.set) {
      Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', {
        set: function (v) { _descr.set.call(this, v + 0.001); },
        get: function () { return _descr.get.call(this); },
        configurable: true,
      });
    }
  } catch (e) {}

  // ④ settle(t)：Web Animations / CSS 动画钉到 t*1000 并暂停
  window.__omotionSettle = function (t) {
    if (document.getAnimations) {
      document.getAnimations().forEach(function (a) {
        try { a.currentTime = t * 1000; a.pause(); } catch (e) {}
      });
    }
  };

  window.__omotionFreezeClock = _freeze;
  window.__omotionClockFrozen = true;
})();
"""

# 时钟/随机风险标记（与 quality_gate CLOCK_RISK_PATTERNS 同源，仅判定用）
RISK_MARKERS = (
    "Date.now",
    "new Date(",
    "performance.now",
    "requestAnimationFrame",
    "Math.random",
)

# 判定源码集合时排除的目录（模板实例内的依赖/缓存，非模板自身源码）
_EXCLUDED_DIRS = {"node_modules", ".git", "_tmp", "__pycache__"}

_PRELUDE_MARKER = "__omotionClockFrozen"


def has_clock_risk(source: str) -> bool:
    """源码是否命中时钟/随机风险标记（命中才需要时钟冻结）。"""
    return any(m in source for m in RISK_MARKERS)


def collect_sources(directory: str | Path) -> str:
    """递归采集模板实例的 HTML/JS/MJS 源码拼接（用于风险判定）。

    排除 node_modules/.git/_tmp/__pycache__ 等非模板源码目录；
    时钟依赖常位于入口 HTML 引用的外部 JS（如 canvas 型 native/film.js），
    仅扫入口 HTML 会漏判，必须按源码集合判定。
    """
    root = Path(directory)
    if not root.is_dir():
        return ""
    chunks = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in _EXCLUDED_DIRS for part in rel.parts):
            continue
        if p.suffix.lower() not in (".html", ".js", ".mjs"):
            continue
        try:
            chunks.append(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
    return "\n".join(chunks)


def inject_clock_freeze(html: str) -> str:
    """把时钟冻结 prelude 注入 HTML 的 <head> 最前（模板脚本之前）。

    幂等：已含 prelude 标记时原样返回。
    """
    if _PRELUDE_MARKER in html:
        return html
    script = f"<script>\n{CLOCK_FREEZE_JS}\n</script>"
    m = re.search(r"<head[^>]*>", html, re.IGNORECASE)
    if m:
        return html[:m.end()] + "\n" + script + html[m.end():]
    m = re.search(r"<html[^>]*>", html, re.IGNORECASE)
    if m:
        return html[:m.end()] + "\n" + script + html[m.end():]
    return script + "\n" + html


def find_entry_html(directory: str | Path) -> list[Path]:
    """发现模板实例的入口 HTML（index.html / native/index.html，兜底 rglob）。

    返回已排序的候选列表；注入器按此列表顺序注入。
    """
    root = Path(directory)
    if not root.is_dir():
        return []
    found = []
    for rel in ("index.html", "native/index.html"):
        p = root / rel
        if p.is_file():
            found.append(p)
    if found:
        return found
    return sorted(p for p in root.rglob("index.html") if p.is_file())


def apply_clock_freeze(project_dir: str | Path) -> list[str]:
    """对 project_dir 中命中时钟风险的模板实例注入 prelude。

    判定基于整目录源码集合（含入口 HTML 引用的外部 JS），注入点仍为
    入口 HTML 的 <head> 最前。返回注入成功的 HTML 文件名列表
    （空 = 无需注入）。
    """
    root = Path(project_dir)
    if not root.is_dir():
        return []
    if not has_clock_risk(collect_sources(root)):
        return []
    injected = []
    for html in find_entry_html(root):
        try:
            text = html.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        new = inject_clock_freeze(text)
        if new != text:
            html.write_text(new, encoding="utf-8")
            injected.append(html.name)
    return injected
