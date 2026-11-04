# -*- coding: utf-8 -*-
"""clock_freeze（P0-b 时钟冻结 prelude）单元测试。"""

from pathlib import Path

from core.renderer.clock_freeze import (
    CLOCK_FREEZE_JS,
    apply_clock_freeze,
    find_entry_html,
    has_clock_risk,
    inject_clock_freeze,
)

RISKY_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <script src="app.js"></script>
</head>
<body>
  <canvas id="c"></canvas>
  <script>
    const t0 = Date.now();
    const r = Math.random();
    function loop(ts) { requestAnimationFrame(loop); }
  </script>
</body>
</html>
"""

CLEAN_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body><canvas id="c"></canvas></body>
</html>
"""


def test_prelude_content_covers_film_clock_points():
    """prelude 必须覆盖 FILM_CLOCK 五要点（时间戳/Date/random/rAF/settle）。"""
    assert "__omotionFreezeClock" in CLOCK_FREEZE_JS
    assert "performance.now = function" in CLOCK_FREEZE_JS
    assert "Date.now = function" in CLOCK_FREEZE_JS
    assert "Math.random = function" in CLOCK_FREEZE_JS
    assert "requestAnimationFrame" in CLOCK_FREEZE_JS
    assert "__omotionSettle" in CLOCK_FREEZE_JS
    assert "currentTime" in CLOCK_FREEZE_JS  # 媒体帧边界 +0.001


def test_inject_into_head_first():
    """prelude 插到 <head> 最前（在模板脚本之前执行）。"""
    out = inject_clock_freeze(RISKY_HTML)
    head_end = out.index("</head>")
    app_js = out.index('src="app.js"')
    prelude = out.index("__omotionClockFrozen")
    assert prelude < app_js < head_end


def test_inject_idempotent():
    """重复注入不产生重复 prelude。"""
    once = inject_clock_freeze(RISKY_HTML)
    twice = inject_clock_freeze(once)
    assert twice == once
    assert once.count("// omotion clock-freeze prelude") == 1


def test_inject_no_head():
    """无 <head> 时插到 <html> 之后 / 文档最前。"""
    bare = "<html><body>hi</body></html>"
    out = inject_clock_freeze(bare)
    assert out.startswith("<html>")
    assert out.index("__omotionClockFrozen") < out.index("<body>")


def test_has_clock_risk():
    assert has_clock_risk(RISKY_HTML)
    assert not has_clock_risk(CLEAN_HTML)


def test_find_entry_html_root(tmp_path: Path):
    (tmp_path / "index.html").write_text(CLEAN_HTML, encoding="utf-8")
    found = find_entry_html(tmp_path)
    assert [p.name for p in found] == ["index.html"]


def test_find_entry_html_native(tmp_path: Path):
    native = tmp_path / "native"
    native.mkdir()
    (native / "index.html").write_text(CLEAN_HTML, encoding="utf-8")
    found = find_entry_html(tmp_path)
    assert [p.relative_to(tmp_path).as_posix() for p in found] == [
        "native/index.html"
    ]


def test_find_entry_html_fallback_rglob(tmp_path: Path):
    sub = tmp_path / "src" / "pages"
    sub.mkdir(parents=True)
    (sub / "index.html").write_text(CLEAN_HTML, encoding="utf-8")
    found = find_entry_html(tmp_path)
    assert [p.name for p in found] == ["index.html"]


def test_find_entry_html_missing(tmp_path: Path):
    assert find_entry_html(tmp_path) == []


def test_apply_clock_freeze_injects_only_risky(tmp_path: Path):
    (tmp_path / "index.html").write_text(RISKY_HTML, encoding="utf-8")
    injected = apply_clock_freeze(tmp_path)
    assert injected == ["index.html"]
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "__omotionClockFrozen" in text


def test_apply_clock_freeze_skips_clean(tmp_path: Path):
    (tmp_path / "index.html").write_text(CLEAN_HTML, encoding="utf-8")
    injected = apply_clock_freeze(tmp_path)
    assert injected == []
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "__omotionClockFrozen" not in text


def test_apply_clock_freeze_detects_external_js(tmp_path: Path):
    """时钟依赖位于入口 HTML 引用的外部 JS 时也必须注入（E7 canvas 型）。"""
    (tmp_path / "index.html").write_text(CLEAN_HTML, encoding="utf-8")
    (tmp_path / "film.js").write_text(
        "const t = performance.now(); requestAnimationFrame(loop);",
        encoding="utf-8",
    )
    injected = apply_clock_freeze(tmp_path)
    assert injected == ["index.html"]
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "// omotion clock-freeze prelude" in text


def test_apply_clock_freeze_ignores_node_modules(tmp_path: Path):
    """node_modules 内的时钟代码不算模板源码（不触发注入）。"""
    (tmp_path / "index.html").write_text(CLEAN_HTML, encoding="utf-8")
    nm = tmp_path / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "lib.js").write_text("performance.now();", encoding="utf-8")
    injected = apply_clock_freeze(tmp_path)
    assert injected == []
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "__omotionClockFrozen" not in text


def test_apply_clock_freeze_idempotent(tmp_path: Path):
    (tmp_path / "index.html").write_text(RISKY_HTML, encoding="utf-8")
    apply_clock_freeze(tmp_path)
    injected = apply_clock_freeze(tmp_path)
    assert injected == []  # 已注入 → 不再重复
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert text.count("// omotion clock-freeze prelude") == 1
