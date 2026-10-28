"""V2 params builder - contract-driven parameter construction for v2 templates.

Two v2 runtime families (E7-verified 2026-10-28):
- split-root (narration): params = {"media": {talk, captions}, "brief": <adu-auto-brief/1>}
- canvas-split-root (native-frame): params = {sourceEdits, voiceover, voiceoverMode,
  soundtrackMode, music, media(14 slots), copy(229 slots), brand}

Builders read contracts from the *extracted* template core (instance/template
plus the locale core captured under zh/), so callers do not hand-craft JSON.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Contract discovery
# ---------------------------------------------------------------------------

def find_core_dir(extracted_dir) -> Path:
    """Locate the contract-bearing core dir for a v2 template.

    Priority:
    1. instance/core - captured decrypted locale core (engine._capture_v2_core)
    2. locale core under extracted/zh|en (motiongit-locale-core-*)
    3. extracted root with contracts
    """
    extracted = Path(extracted_dir)
    captured = extracted / 'core'
    if captured.is_dir():
        # captured core may itself contain a nested locale core
        for loc in ('zh', 'en'):
            cand = captured / loc
            if cand.is_dir() and (cand / 'params.schema.json').exists():
                return cand
        if (captured / 'params.schema.json').exists():
            return captured
        # captured = copy of locale core root; descend one level if needed
        for child in sorted(captured.iterdir()):
            if child.is_dir() and (child / 'params.schema.json').exists():
                return child
    for locale in ('zh', 'en'):
        lroot = extracted / locale
        if not lroot.is_dir():
            continue
        for child in sorted(lroot.iterdir()):
            if child.is_dir() and (child / 'params.schema.json').exists():
                return child
        if (lroot / 'params.schema.json').exists():
            return lroot
    return extracted


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8', errors='replace'))


def contract_path(core: Path, name: str) -> Optional[Path]:
    """Locate a contract file: core root, then core/adapter (native-frame)."""
    for base in (core, core / 'adapter'):
        p = base / name
        if p.exists():
            return p
    return None


def detect_family(extracted_dir) -> str:
    """Return 'canvas' (native-frame) or 'talk' (split-root narration)."""
    extracted = Path(extracted_dir)
    core = find_core_dir(extracted)
    if (core / 'native-config.json').exists():
        return 'canvas'
    return 'talk'


# ---------------------------------------------------------------------------
# Canvas family: media(14) + copy(229) + brand + audio switches
# ---------------------------------------------------------------------------

def build_canvas_params(extracted_dir, media_dir=None, copy_overrides=None,
                        brand=None, sweep_word='O_Motion') -> dict:
    """Build a renderable canvas-family params dict from template contracts.

    Args:
        extracted_dir: extracted template dir (instance/template)
        media_dir: dir with per-slot PNG files (fallback: auto-generate solids)
        copy_overrides: {section: {slot: text}} to override auto-filled copy
        brand: custom brand dict (fallback: defaults derived from contracts)
        sweep_word: brand sweep word (1-16 chars)

    Returns:
        params dict matching params.schema.json required fields.
    """
    extracted = Path(extracted_dir)
    core = find_core_dir(extracted)
    native_cfg = load_json(contract_path(core, 'native-config.json')) if contract_path(core, 'native-config.json') else {}
    schema = load_json(contract_path(core, 'params.schema.json')) if contract_path(core, 'params.schema.json') else {}
    copy_contract = load_json(contract_path(core, 'copy-contract.json')) if contract_path(core, 'copy-contract.json') else None

    # --- media: 14 slots from native-config mediaSlots ---
    media_slots = native_cfg.get('mediaSlots', {})
    if not media_slots:
        # fallback: schema media properties
        media_slots = {k: {} for k in (schema.get('properties', {}).get('media', {})
                                       .get('required', []))}
    media = {}
    media_root = Path(media_dir) if media_dir else None
    for slot, spec in media_slots.items():
        w = spec.get('width', 416)
        h = spec.get('height', 234)
        media[slot] = {
            'src': f'assets/user/{slot}.png',
            'kind': 'image',
            'start': 0,
            'fit': 'contain',
            'focusX': 0.5,
            'focusY': 0.5,
        }
        # ensure a real file exists under media_dir (or generate solid)
        if media_root is not None:
            f = media_root / f'{slot}.png'
            if not f.exists():
                _generate_solid_png(f, w, h)
        elif not media_root:
            pass  # caller must stage files; src is relative assets/user/

    # --- copy: 229 slots from copy-contract (auto-fill + overrides) ---
    copy = {}
    section_topics = {
        'partners': '技术伙伴', 'demo': '产品演示', 'hero': 'O_Motion AI 视频创作平台',
        'recipe': '创作配方', 'open': '开始使用', 'pack': '功能包', 'flow': '创作流程',
        'adapt': '灵活适配', 'reuse': '复用能力', 'spec': '规格说明', 'share': '分享成果',
        'finale': '马上开始', 'end': '谢谢观看', 'hud': 'O_Motion', 'labels': '标签',
    }
    if copy_contract:
        for sec, items in copy_contract.get('slots', {}).items():
            copy[sec] = {}
            topic = section_topics.get(sec, sweep_word)
            for name, slot in items.items():
                mc = slot.get('maxCharacters', 64)
                if slot.get('kind') == 'kinetic':
                    copy[sec][name] = ('{' + topic + '}')[:mc] if mc >= 4 else topic[:max(1, mc)]
                else:
                    base = sweep_word if sec in ('labels', 'hud') else topic
                    copy[sec][name] = base[:mc] if len(base) > mc else base
    if copy_overrides:
        for sec, items in copy_overrides.items():
            copy.setdefault(sec, {})
            copy[sec].update(items)

    # --- brand ---
    if brand is None:
        brand = _default_brand(core, sweep_word)
    brand.setdefault('sweepWord', sweep_word)

    # --- audio: quietest path (no voice, no music/effects) ---
    params = {
        'sourceEdits': [],
        'voiceover': '',
        'voiceoverMode': 'none',
        'soundtrackMode': 'none',
        'music': '',
        'media': media,
        'copy': copy,
        'brand': brand,
    }
    return params


def _default_brand(core: Path, sweep_word: str) -> dict:
    """Derive default brand: vector outline + builtin tools + artwork solids."""
    artwork_roles = ['official', 'wordmark', 'partner1', 'partner2', 'partner3',
                     'partner4', 'partner5', 'partner5Light']
    return {
        'sweepWord': sweep_word,
        'leadingDot': True,
        'artwork': {r: {'src': f'assets/user/brand-{r}.png', 'fit': 'contain'}
                    for r in artwork_roles},
        'tools': {r: {'kind': 'builtin', 'name': r}
                  for r in ('cli', 'render', 'edit', 'play', 'more')},
        'outline': {'mode': 'vector', 'dot': {'cx': 40, 'cy': 40, 'r': 6},
                    'path': 'M 10 80 C 40 10 65 10 95 80 S 150 150 180 80'},
    }


# ---------------------------------------------------------------------------
# Talk family: media(talk+captions) + brief(adu-auto-brief/1)
# ---------------------------------------------------------------------------

def build_talk_params(extracted_dir, narration_text, talk_video_src=None,
                      captions=None, grain_src=None,
                      fps=60, words_per_sec=3.5) -> dict:
    """Build a renderable talk-family params dict.

    E7-verified shape: params = {"media": {...}, "brief": <adu-auto-brief/1>}
    brief requires: contentEvidence(text+textSha256+units) + segments(06-A
    reveal-frame -> making-card) + counts.labels + anchors.entry.

    Args:
        extracted_dir: extracted template dir
        narration_text: full narration text (Chinese/English, punctuation kept)
        talk_video_src: relative instance path to presenter/talk video
            (e.g. 'assets/user/talk.mp4'); None -> poster-style params
        captions: list of caption dicts (id,t0,t1,zh,en,highlight); if None,
            auto-built with uniform timing and asr-word-boundaries highlight
        grain_src: optional grain texture relative path
        fps: output fps (default 60)
        words_per_sec: narration pacing for auto segment timing

    Returns:
        params dict in v2 talk-family shape.
    """
    core = find_core_dir(extracted_dir)
    media = {}
    if talk_video_src:
        # E7-verified: media.talk is the file NAME relative to inputs root
        media['talk'] = Path(talk_video_src).name
    if captions is None:
        captions = _auto_captions(narration_text, words_per_sec)
    # scale caption timeline to a TWO-BLOCK clock so segment boundaries sit
    # exactly on native scene boundaries (E7 verified: reveal-frame 0-5.1334s
    # @308 frames, making-card 5.1334-10.6s @328 frames)
    captions = _plan_caption_timeline(captions, fps, seg1_frames=308, seg2_frames=328)
    # E7-verified: media.captions references a JSON file in inputs root
    media['captions'] = 'captions.json'
    # NOTE: media must be EXACTLY {talk, captions} (run.py bindings);
    # grainTexture belongs in brief segments candidates inputs, not media.

    brief = _build_brief(extracted_dir, narration_text, captions,
                         fps=fps, words_per_sec=words_per_sec,
                         talk_video_src=talk_video_src)
    return {'media': media, 'brief': brief, '_captions': captions}


def _segment_caption_split(captions: list, seg1_frames: int, total_frames: int) -> int:
    """Choose the caption index whose cumulative character share is closest to
    the seg-1 frame share (so ownership aligns with native scene duration)."""
    share = seg1_frames / max(1, total_frames)
    chars = [len(c.get('zh', '')) for c in captions]
    total = sum(chars) or 1
    cum = 0.0
    best_k, best_diff = 0, abs(share)
    for i, k in enumerate(chars):
        cum += k / total
        d = abs(cum - share)
        if d < best_diff and i < len(captions) - 1:
            best_k, best_diff = i + 1, d
    return best_k


def _plan_caption_timeline(captions: list, fps: int,
                           seg1_frames: int = 308, seg2_frames: int = 328) -> list:
    """Rebuild the caption clock as two contiguous blocks whose boundary sits
    exactly on the native scene boundary (E7 verified values for template 385712
    talk kit): block-1 -> [0, seg1_frames/fps], block-2 -> [seg1_frames/fps,
    (seg1_frames+seg2_frames)/fps]. Each caption keeps its character-relative
    pacing; highlight units get the same continuous clock.
    """
    seg1_end = seg1_frames / fps
    seg2_end = (seg1_frames + seg2_frames) / fps
    k = _segment_caption_split(captions, seg1_frames, seg1_frames + seg2_frames)
    block1 = captions[:k]
    block2 = captions[k:]
    out = []
    t = 0.0
    for idx, (block, end_t) in enumerate(((block1, seg1_end), (block2, seg2_end))):
        block_dur = sum(c['t1'] - c['t0'] for c in block) or 1.0
        scale = (end_t - t) / block_dur
        for c in block:
            zh = c.get('zh', '')
            dur = (c['t1'] - c['t0']) * scale
            c0, c1 = round(t, 3), round(t + dur, 3)
            hu = c.get('highlight', {}).get('units', [])
            units = []
            unit_t = t
            for u in hu:
                ud = (u.get('end', u.get('start', 0)) - u.get('start', 0)) * scale
                units.append({'text': u.get('text', ''),
                              'start': round(unit_t, 3),
                              'end': round(unit_t + ud, 3)})
                unit_t += ud
            out.append({'id': c.get('id', ''), 't0': c0, 't1': c1,
                        'zh': zh, 'en': c.get('en', ''),
                        'highlight': {'source': 'asr-word-boundaries',
                                      'language': 'zh', 'units': units}})
            t += dur
        t = end_t
    return out


def stage_talk_assets(instance_inputs, talk_video_src=None, captions=None,
                      grain_src=None) -> None:
    """Stage talk-family media into instance inputs root.

    - talk video: copied from talk_video_src
    - captions: written to captions.json
    - grain: copied from grain_src
    """
    instance_inputs = Path(instance_inputs)
    instance_inputs.mkdir(parents=True, exist_ok=True)
    if talk_video_src:
        src = Path(talk_video_src)
        dst = instance_inputs / src.name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
    if captions is not None:
        (instance_inputs / 'captions.json').write_text(
            json.dumps(captions, ensure_ascii=False), encoding='utf-8')
    if grain_src:
        src = Path(grain_src)
        dst = instance_inputs / src.name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)


_ZH_EN = {
    '欢迎': 'Welcome', '了解': 'learn about', '我们': 'our', '的': '', '新产品': 'new product',
    '功能强大': 'powerful', '价格实惠': 'affordable', '快来体验': 'come and try',
    '马上开始': 'start now', '体验': 'experience', '开始': 'start', '快': 'hurry',
    '来': 'come', '产品': 'product', '价格': 'price', '实惠': 'affordable',
    '功能': 'features', '强大': 'powerful', '马': 'right', '上': 'now',
}


def _zh_to_en(zh: str) -> str:
    """Minimal phrase-level translation for caption English line (non-empty)."""
    s = zh
    for k, v in _ZH_EN.items():
        s = s.replace(k, v)
    s = s.strip('，。！？；、,.!?;:： ')
    return s if s else 'Caption line'


def _auto_captions(text: str, words_per_sec: float) -> list:
    """Split narration into caption chunks with asr-word-boundaries.

    E7-verified shape: {id,t0,t1,zh,en,highlight{source,language,units}}
    - zh keeps trailing punctuation (must consume original text exactly)
    - highlight.units use GLOBAL timeline seconds (start/end continuous)
    """
    # split into sentences/clauses at punctuation
    parts = re.split(r'(?<=[。，！？；、,.!?;])', text)
    parts = [p for p in parts if p.strip()]
    captions = []
    t = 0.0
    for i, p in enumerate(parts):
        chars = len(p)
        dur = max(0.8, chars / words_per_sec)
        zh = p.strip()
        en = _zh_to_en(zh)
        # word-level units with continuous global timeline
        units = []
        unit_t = t
        for ch in zh:
            unit_dur = dur / max(1, len(zh))
            units.append({'text': ch, 'start': round(unit_t, 3),
                          'end': round(unit_t + unit_dur, 3)})
            unit_t += unit_dur
        captions.append({
            'id': str(i + 1),
            't0': round(t, 3),
            't1': round(t + dur, 3),
            'zh': zh,
            'en': en,
            'highlight': {
                'source': 'asr-word-boundaries',
                'language': 'zh',
                'units': units,
            },
        })
        t += dur
    return captions


def _build_brief(extracted_dir, text, captions, fps=60, words_per_sec=3.5,
                 talk_video_src=None) -> dict:
    """Build adu-auto-brief/1 brief (E7-verified flat structure).

    Shape (mirrors E7 e7_v8.py success):
      {schema: 'adu-auto-brief/1', brand, fps, layout, narrationDuration,
       music: {mode: none}, contentEvidence: {schema, text, textSha256, units},
       segments: [{id, text, intent, durationFrames, ownershipRefs, counts,
                   anchors, candidates}]}
    - contentEvidence units: char spans {id=sha16:start:end, start, end, text,
      optional time:{start,end}}; concatenated text == narration exactly
    - segments: reveal-frame -> making-card (only making-card may finish)
    - candidates inputs.labels carry ACTUAL label texts (not paths)
    """
    import hashlib
    talk_name = Path(talk_video_src).name if talk_video_src else 'talk.mp4'
    evidence = _content_evidence(text, captions)
    # scene capabilities come from the template capacity matrix (E7 verified:
    # reveal-frame minFrames=308 / source 0-5.1334s; making-card minFrames=328)
    SEG1_FRAMES, SEG2_FRAMES = 308, 328
    total_dur = max(1.0, sum(c['t1'] - c['t0'] for c in captions))
    dur_frames = SEG1_FRAMES + SEG2_FRAMES
    narration_dur = round(dur_frames / fps, 3)

    n_units = len(evidence['units'])
    # ownership by caption groups: captions before boundary -> seg-1
    best_k = _segment_caption_split(captions, SEG1_FRAMES, dur_frames)
    seg1_units, seg2_units = [], []
    seen = 0
    for i, c in enumerate(captions):
        zh = c.get('zh', '')
        k = len(zh)
        chunk_units = evidence['units'][seen:seen + k]
        (seg1_units if i < best_k else seg2_units).extend(chunk_units)
        seen += k
    # safety: every unit owned exactly once, original order preserved
    assert len(seg1_units) + len(seg2_units) == n_units
    seg1_refs = [u['id'] for u in seg1_units]
    seg2_refs = [u['id'] for u in seg2_units]
    seg1_text = ''.join(u['text'] for u in seg1_units)
    seg2_text = ''.join(u['text'] for u in seg2_units)

    brief = {
        'schema': 'adu-auto-brief/1',
        'brand': 'O_Motion',
        'fps': fps,
        'layout': 'landscape',
        'narrationDuration': narration_dur,
        'music': {'mode': 'none'},
        'contentEvidence': {
            'schema': 'adu-content-evidence/1',
            'text': evidence['text'],
            'textSha256': evidence['textSha256'],
            'units': evidence['units'],
        },
        'segments': [
            {
                'id': 'seg-1',
                'text': seg1_text,
                'intent': 'reveal-frame',
                'durationFrames': SEG1_FRAMES,
                'ownershipRefs': seg1_refs,
                'counts': {'labels': 11},
                'anchors': {'entry': {'frame': 0}},
                'candidates': {
                    '06-A:reveal-frame': {
                        'inputs': {'labels': _reveal_labels(),
                                   'grainTexture': 'grain.png',
                                   'videos': {'ins1': talk_name}},
                        'slots': {'presenter': '@talk'},
                    },
                },
            },
            {
                'id': 'seg-2',
                'text': seg2_text,
                'intent': 'making-card',
                'durationFrames': SEG2_FRAMES,
                'ownershipRefs': seg2_refs,
                'counts': {'labels': 3},
                'anchors': {'entry': {'frame': 0}},
                'candidates': {
                    '06-A:making-card': {
                        'inputs': {'labels': ['快来体验', '马上开始', 'O_Motion'],
                                   'grainTexture': 'grain.png'},
                        'slots': {'presenter': '@talk'},
                    },
                },
            },
        ],
    }
    return brief


def _content_evidence(text: str, captions: list = None) -> dict:
    """Build contentEvidence: text + textSha256 + char-span units.

    Units carry optional global timeline (E7-verified): commas/punct without
    time are legal as long as ownership is explicit in segments.
    """
    import hashlib
    sha = hashlib.sha256(text.encode('utf-8')).hexdigest()
    # per-char global timeline sourced FROM captions highlight units (same clock)
    time_of = {}
    if captions:
        for c in captions:
            zh = c.get('zh', '')
            start_idx = text.find(zh)
            if start_idx < 0:
                continue
            hu = c.get('highlight', {}).get('units', [])
            for i, ch in enumerate(zh):
                if i < len(hu):
                    t0, t1 = hu[i].get('start'), hu[i].get('end')
                    if t0 is not None and t1 is not None and ch not in '，。！？；、,.!?;:：':
                        time_of[start_idx + i] = (float(t0), float(t1))
    units = []
    pos = 0
    for ch in text:
        u = {'id': f'{sha[:16]}:{pos}:{pos + 1}',
             'text': ch, 'start': pos, 'end': pos + 1}
        if pos in time_of:
            t0, t1 = time_of[pos]
            u['time'] = {'start': round(t0, 3), 'end': round(t1, 3)}
        units.append(u)
        pos += 1
    return {'text': text, 'textSha256': sha, 'units': units}


def _reveal_labels() -> list:
    """Default reveal-frame label texts (E7-verified 11-slot shape)."""
    return ['开场展开', '重点', 'MAKE IT VISIBLE', '// 高对比海报 · 内容优先',
            '观点', '方法', '知识', '技术', '案例', '经验', '展开表达']


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_solid_png(path: Path, width: int, height: int) -> None:
    """Generate a solid-color PNG via ffmpeg (requires ffmpeg 9 on PATH)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        'ffmpeg', '-y', '-f', 'lavfi',
        '-i', f'color=c=0x1a202c:s={width}x{height}:r=1:d=1',
        '-frames:v', '1', str(path),
    ], check=True, capture_output=True)


def stage_canvas_assets(instance_inputs: Path, media_map: dict,
                        brand: dict, colors=None) -> None:
    """Copy/generate all assets referenced by canvas params into inputs.

    media_map: {slot: (width, height)} for solid generation
    brand: brand dict (artwork srcs collected)
    """
    instance_inputs = Path(instance_inputs)
    user_dir = instance_inputs / 'assets' / 'user'
    user_dir.mkdir(parents=True, exist_ok=True)
    colors = colors or ['0x1a202c', '0x2d3748', '0x374151', '0x0f766e',
                        '0x4338ca', '0x7c2d12', '0x831843', '0x1e3a8a']
    for i, (slot, (w, h)) in enumerate(media_map.items()):
        f = user_dir / f'{slot}.png'
        if not f.exists():
            _generate_solid_png(f, w, h)
    for role, spec in brand.get('artwork', {}).items():
        src = spec.get('src', '')
        if src.startswith('assets/user/'):
            f = user_dir / Path(src).name
            if not f.exists():
                _generate_solid_png(f, 200, 80)
