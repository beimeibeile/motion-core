# -*- coding: utf-8 -*-
"""runtime 能力探测（P0-a）单元测试。"""

from core.runtime.capabilities import probe_runtime

CANVAS_TEMPLATE = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<canvas id="c" width="1920" height="1080"></canvas>
<script>
const ctx = document.getElementById('c').getContext('2d');
const params = new URLSearchParams(location.search);
const t0 = performance.now();
if (params.get('preview')) { startPlayer(); }
function startPlayer() { requestAnimationFrame(loop); }
function loop(ts) { requestAnimationFrame(loop); }
function renderAt(t) {
  const frame = Math.round(t * 60);
  ctx.fillRect(frame % 100, 0, 10, 10);
}
</script>
</body>
</html>
"""

FRAME_ENTRY_TEMPLATE = """<!DOCTYPE html>
<html>
<body><canvas id="c"></canvas>
<script>
function renderFrame(frame) {
  const t = frame / 60;
  ctx.fillRect(t, 0, 5, 5);
}
</script>
</body>
</html>
"""

DOM_TEMPLATE = """<!DOCTYPE html>
<html>
<body>
<div id="title"></div>
<script>
const el = document.getElementById('title');
el.style.opacity = '1';
</script>
</body>
</html>
"""


def _write(tmp_path, html, motion_json=None):
    (tmp_path / "index.html").write_text(html, encoding="utf-8")
    if motion_json:
        (tmp_path / "motion.json").write_text(motion_json, encoding="utf-8")
    return tmp_path


def test_canvas_render_at_seconds(tmp_path):
    root = _write(
        tmp_path, CANVAS_TEMPLATE,
        motion_json='{"name": "tpl", "fps": 60}',
    )
    r = probe_runtime(root)
    assert r["kind"] == "canvas"
    assert r["render_entry"] == "renderAt"
    assert r["param_unit"] == "frames"  # Math.round(t * 60) → 帧号换算
    assert r["supports_t"] is False  # 该模板未读 ?t=
    assert r["supports_preview"] is True
    assert r["clock_risk"] is True  # performance.now + rAF
    assert r["fps"] == 60
    assert r["entry_html"] == "index.html"


def test_render_frame_frames(tmp_path):
    root = _write(tmp_path, FRAME_ENTRY_TEMPLATE)
    r = probe_runtime(root)
    assert r["render_entry"] == "renderFrame"
    assert r["param_unit"] == "frames"
    assert r["kind"] == "canvas"


def test_dom_kind(tmp_path):
    root = _write(tmp_path, DOM_TEMPLATE)
    r = probe_runtime(root)
    assert r["kind"] == "dom"
    assert r["render_entry"] is None


def test_no_entry(tmp_path):
    root = _write(tmp_path, "<html><body>hi</body></html>")
    r = probe_runtime(root)
    assert r["render_entry"] is None
    assert r["param_unit"] == "unknown"
    assert r["clock_risk"] is False


def test_supports_t_param(tmp_path):
    html = """<html><body><canvas id="c"></canvas><script>
const t = new URLSearchParams(location.search).get('t');
function renderAt(t) { ctx.fillRect(t * 1000, 0, 5, 5); }
</script></body></html>"""
    root = _write(tmp_path, html)
    r = probe_runtime(root)
    assert r["supports_t"] is True
    assert r["param_unit"] == "seconds"  # t * 1000 → 毫秒


def test_empty_dir(tmp_path):
    r = probe_runtime(tmp_path)
    assert r["kind"] == "unknown"
    assert r["render_entry"] is None
    assert r["fps"] is None
