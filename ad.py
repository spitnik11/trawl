"""ad.py — Trawl ad-brief -> animated HTML ad -> mp4.  Rung 1 of the ad prompting engine.

Stdlib only. External runtime deps are the same two the rest of trawl already leans on:
a headless Chrome/Edge (via shot.py) and ffmpeg on PATH. No pip installs.

This is the TYPE LAYER: the headline/CTA/brand are crisp DOM text, animated with CSS, and
NEVER diffused (the two-layer law). Rung 2+ slides an AI visual underneath; Rung 1 ships the
type layer alone as a finished, postable motion ad.

Frame capture trick (no CDP): shot.capture(wait=t_ms) maps to Chrome's --virtual-time-budget,
so loading the same animated page with an increasing budget freezes the animation at time t.
One screenshot per frame; ffmpeg stitches them.

    from ad import make_ad, demo_brief
    make_ad(demo_brief(), "out")            # -> out/<slug>.mp4

    python ad.py demo                       # render the sample ad
    python trawl.py ad [brief.json]         # wired into the trawl CLI

ponytail: one headless launch per frame (~1-2s each). Fine for short ads; if render time
matters, upgrade the capture loop to a single CDP Page.startScreencast session.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from string import Template

import shot  # sibling module, stdlib-only headless screenshot

# ============================================================================
# CREATIVE MATRIX — the Creatify frameworks (MIT), encoded verbatim so the brief
# engine has real angles/formulas to draw on rather than inventing them.
# ============================================================================

ANGLES = [
    "Problem/Pain Point", "Transformation/Result", "Social Proof", "Comparison/Alternative",
    "Urgency/Scarcity", "Authority/Expert", "Price/Value", "Curiosity/Intrigue",
    "Fear of Missing Out (FOMO)", "Lifestyle/Aspirational", "Education/How-To",
    "Emotional/Story", "Seasonal/Timely", "Risk Reversal", "Ingredient/Feature Spotlight",
    "User-Generated/Review Highlight",
]

# aspect -> (w, h) [even dims for yuv420p]; optimal length + default sound from the platform matrix.
ASPECTS = {"1:1": (1080, 1080), "4:5": (1080, 1350), "9:16": (1080, 1920), "16:9": (1920, 1080)}
PLATFORM_MATRIX = {
    "tiktok":   {"aspect": "9:16", "optimal_s": 20, "sound": "on"},
    "reels":    {"aspect": "9:16", "optimal_s": 20, "sound": "off"},
    "instagram":{"aspect": "4:5", "optimal_s": 15, "sound": "off"},
    "facebook": {"aspect": "1:1", "optimal_s": 20, "sound": "off"},
    "shorts":   {"aspect": "9:16", "optimal_s": 30, "sound": "on"},
    "linkedin": {"aspect": "16:9", "optimal_s": 15, "sound": "off"},
    "x":        {"aspect": "16:9", "optimal_s": 15, "sound": "off"},
}

# Speed/time presets — the VISUAL LAYER is what costs time. Estimates are rough on the 12GB card.
# fast = no GPU; still = one Z-Image render (~16s); motion = a full Wan clip (~4-5 min).
MODES = {
    "fast":   {"label": "Fast",   "visual_model": None,      "fps": 24, "est": "~1 min",
               "blurb": "Animated type only — no GPU"},
    "still":  {"label": "Still",  "visual_model": "z-image", "fps": 24, "est": "~2 min",
               "blurb": "AI image behind motion text"},
    "motion": {"label": "Motion", "visual_model": "wan",     "fps": 30, "est": "~5 min",
               "blurb": "AI video b-roll (Wan)"},
}
DEFAULT_MODE = "fast"


def mode_overrides(mode: str) -> dict:
    """Brief overrides for a speed/time preset (visual_model + fps)."""
    m = MODES.get(mode, MODES[DEFAULT_MODE])
    return {"visual_model": m["visual_model"], "fps": m["fps"]}


# ============================================================================
# BRIEF
# ============================================================================

def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in s.lower()).strip("-")[:48] or "ad"


def new_brief(**over) -> dict:
    """A validated ad brief with sane defaults. Override any field via kwargs."""
    b = {
        "angle": "Transformation/Result",
        "eyebrow": "AI · BUILT FOR OPERATORS",
        "hook": "What if launch content built itself?",
        "headline": "Ship the ad before the meeting ends.",
        "subhead": "One idea in. A finished, on-brand motion ad out. No designer, no timeline.",
        "cta": "See it work",
        "visual_direction": "Text-Heavy",
        "visual_model": None,        # None = type-layer only (Rung 1). "z-image"/"krea"/"illustrious" = render a still visual layer (Rung 2)
        "visual_subject": "",        # what the still shows (defaults to a generic product if blank)
        "verify": True,              # best-effort Florence-2 gate on the rendered still
        "platform": "x",
        "aspect": None,          # None -> derive from platform
        "duration": 4.0,         # seconds
        "fps": 24,
        "brand": {
            "name": "TRAWL",
            "accent": "#3ecf8e",     # signal green; swap per brand
            "bg": "#0a0b0d",
            "fg": "#f4f5f7",
            "palette_words": "emerald green and deep charcoal",  # steers the AI visual layer
        },
    }
    b.update(over)
    b["brand"] = {**b["brand"], **over.get("brand", {})} if "brand" in over else b["brand"]
    if b["aspect"] is None:
        b["aspect"] = PLATFORM_MATRIX.get(b["platform"], {"aspect": "16:9"})["aspect"]
    _validate(b)
    return b


def _validate(b: dict) -> None:
    if b["angle"] not in ANGLES:
        raise ValueError(f"angle {b['angle']!r} not in the 16 Creatify angles")
    if b["aspect"] not in ASPECTS:
        raise ValueError(f"aspect {b['aspect']!r} not one of {list(ASPECTS)}")
    if not b["headline"].strip():
        raise ValueError("headline is empty — the ad has no type layer")
    if b["duration"] <= 0 or b["fps"] <= 0:
        raise ValueError("duration and fps must be positive")
    for k in ("accent", "bg", "fg"):
        v = b["brand"].get(k, "")
        if not (v.startswith("#") and len(v) in (4, 7)):
            raise ValueError(f"brand.{k}={v!r} is not a #hex colour")


def demo_brief() -> dict:
    return new_brief()


# angle -> a sensible default visual direction, so a one-line note yields a complete brief.
ANGLE_VISUAL = {
    "Problem/Pain Point": "Before/After", "Transformation/Result": "Infographic",
    "Social Proof": "Testimonial Card", "Comparison/Alternative": "Comparison",
    "Urgency/Scarcity": "Text-Heavy", "Authority/Expert": "Hero Product",
    "Price/Value": "Comparison", "Curiosity/Intrigue": "Text-Heavy",
    "Fear of Missing Out (FOMO)": "Lifestyle", "Lifestyle/Aspirational": "Lifestyle",
    "Education/How-To": "Infographic", "Emotional/Story": "Lifestyle",
    "Seasonal/Timely": "Lifestyle", "Risk Reversal": "Text-Heavy",
    "Ingredient/Feature Spotlight": "Ingredient/Feature", "User-Generated/Review Highlight": "UGC Screenshot",
}
_ANGLE_HINTS = [  # crude keyword → angle detection for the direction note
    (("faster", "save time", "in minutes", "autopilot", "result", "grow"), "Transformation/Result"),
    (("vs", "versus", "compare", "alternative", "cheaper", "instead of"), "Comparison/Alternative"),
    (("trusted", "customers", "reviews", "loved", "rated", "join"), "Social Proof"),
    (("struggle", "pain", "tired of", "problem", "stop"), "Problem/Pain Point"),
    (("today", "ends", "last chance", "limited", "now"), "Urgency/Scarcity"),
    (("how to", "guide", "learn", "step"), "Education/How-To"),
]


def _detect_angle(note: str) -> str:
    n = (note or "").lower()
    for keys, angle in _ANGLE_HINTS:
        if any(k in n for k in keys):
            return angle
    return "Transformation/Result"


def compose_brief(note: str = "", **over) -> dict:
    """Brief EMITTER: a free-text direction note → a complete, validated ad brief.
    Detects an angle, picks a matching visual direction, seeds copy from the note. Override any field."""
    note = (note or "").strip()
    angle = over.pop("angle", None) or _detect_angle(note)
    fields = dict(
        angle=angle,
        visual_direction=over.pop("visual_direction", None) or ANGLE_VISUAL.get(angle, "Text-Heavy"),
        headline=over.pop("headline", None) or (note[:60].rstrip(" .,") + "." if note else "Built by an AI agent workflow."),
        subhead=over.pop("subhead", None) or "One prompt in. A finished, on-brand ad out.",
        eyebrow=over.pop("eyebrow", None) or "AI · BUILT FOR OPERATORS",
        cta=over.pop("cta", None) or "See it work",
        visual_subject=over.pop("visual_subject", None) or note or "a sleek modern product on a dark surface",
    )
    fields.update(over)   # visual_model, aspect, platform, duration, fps, motion, brand, verify…
    return new_brief(**fields)


def save_to_posts(brief: dict, mp4_path: str, posts_dir) -> str:
    """Drop a finished ad into a Posts/<slug>/ folder (ad.mp4 + post.md) so the review board shows it."""
    slug = _slug(brief["brand"]["name"] + "-" + brief["headline"])
    folder = Path(posts_dir) / slug
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(mp4_path, folder / "ad.mp4")
    (folder / "post.md").write_text(
        f"---\nartifact: {brief['headline']}\ncategory: Ad\nstyle: ad\n"
        f"format: {brief['aspect']} motion\nstatus: ready\n---\n\n"
        f"## Hook\n> {brief['headline']}\n\n## Caption\n{brief['subhead']} {brief.get('cta','')}\n",
        encoding="utf-8")
    return str(folder)


# ============================================================================
# TEMPLATE — dark editorial-tech. All text is DOM (the crisp type layer).
# ============================================================================

_PAGE = Template(r"""<!doctype html><html><head><meta charset="utf-8"><style>
  :root{ --accent:$accent; --bg:$bg; --fg:$fg; --dur:${dur}s; }
  *{margin:0;padding:0;box-sizing:border-box}
  html,body{width:${w}px;height:${h}px;overflow:hidden;background:var(--bg)}
  .stage{position:relative;width:${w}px;height:${h}px;
    font-family:"Helvetica Neue","Segoe UI",system-ui,-apple-system,sans-serif;
    color:var(--fg);
    /* ambient drift keeps late frames alive so the clip never dead-stops */
    background:
      radial-gradient(120% 90% at 18% 8%, color-mix(in srgb, var(--accent) 16%, transparent), transparent 60%),
      radial-gradient(90% 80% at 100% 100%, color-mix(in srgb, var(--accent) 9%, transparent), transparent 55%),
      var(--bg);
    animation:drift var(--dur) ease-in-out infinite alternate;}
  /* dot-grid texture */
  .stage::before{content:"";position:absolute;inset:0;opacity:.5;z-index:0;
    background-image:radial-gradient(color-mix(in srgb,var(--fg) 8%,transparent) 1px,transparent 1px);
    background-size:34px 34px;mask-image:radial-gradient(120% 120% at 50% 40%,#000 40%,transparent 78%)}
  /* VISUAL LAYER: AI-rendered still behind the type, with a scrim so left-side text stays legible */
  .bg{position:absolute;inset:0;z-index:0;background:url("$bg_url") center/cover no-repeat;
    animation:kenburns calc(var(--dur)*2) ease-out both}
  .scrim{position:absolute;inset:0;z-index:1;background:
    linear-gradient(90deg, var(--bg) 4%, color-mix(in srgb,var(--bg) 78%,transparent) 42%, transparent 74%),
    linear-gradient(0deg, color-mix(in srgb,var(--bg) 70%,transparent), transparent 42%)}
  .rule,.wrap,.brandmark{z-index:3}
  @keyframes kenburns{from{transform:scale(1.08)}to{transform:scale(1)}}
  /* accent hairline that draws across */
  .rule{position:absolute;left:7%;top:26%;height:2px;width:0;background:var(--accent);
    box-shadow:0 0 18px color-mix(in srgb,var(--accent) 70%,transparent);
    animation:rule .9s .15s cubic-bezier(.2,.8,.2,1) forwards}
  .wrap{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;
    padding:0 7%;gap:${gap}px}
  .eyebrow{font:600 ${fs_eye}px/1 "SF Mono","Cascadia Code",Consolas,ui-monospace,monospace;
    letter-spacing:.28em;color:var(--accent);text-transform:uppercase;
    opacity:0;animation:rise .7s .05s cubic-bezier(.2,.8,.2,1) forwards}
  h1{font-weight:800;font-size:${fs_h1}px;line-height:.98;letter-spacing:-.03em;max-width:16ch}
  h1 .w{display:inline-block;opacity:0;transform:translateY(.5em) rotate(1.5deg);
    animation:word .6s cubic-bezier(.2,.85,.2,1) forwards}
  .sub{font-size:${fs_sub}px;line-height:1.4;color:color-mix(in srgb,var(--fg) 72%,transparent);
    max-width:34ch;opacity:0;animation:rise .7s .9s cubic-bezier(.2,.8,.2,1) forwards}
  .cta{display:inline-flex;align-items:center;gap:.6em;align-self:flex-start;margin-top:${gap}px;
    font:700 ${fs_cta}px/1 inherit;color:var(--bg);background:var(--accent);
    padding:.7em 1.15em;border-radius:12px;opacity:0;
    animation:rise .6s 1.15s cubic-bezier(.2,.8,.2,1) forwards, pulse 1.8s 1.8s ease-in-out infinite}
  .cta::after{content:"→";font-weight:700}
  .brandmark{position:absolute;left:7%;bottom:6%;
    font:600 ${fs_eye}px/1 "SF Mono","Cascadia Code",Consolas,ui-monospace,monospace;
    letter-spacing:.22em;color:color-mix(in srgb,var(--fg) 55%,transparent);text-transform:uppercase;
    opacity:0;animation:rise .7s 1.3s ease forwards}
  .brandmark b{color:var(--fg)}
  @keyframes word{to{opacity:1;transform:translateY(0) rotate(0)}}
  @keyframes rise{to{opacity:1;transform:none}}
  @keyframes rule{to{width:34%}}
  @keyframes drift{to{background-position:6% 4%, -4% -3%, 0 0}}
  @keyframes pulse{0%,100%{box-shadow:0 0 0 0 color-mix(in srgb,var(--accent) 55%,transparent)}
                   50%{box-shadow:0 0 0 14px transparent}}
  $overlay_css
</style></head><body>
  <div class="stage">
    $bg_layer
    <div class="rule"></div>
    <div class="wrap">
      <div class="eyebrow">$eyebrow</div>
      <h1>$headline_html</h1>
      <div class="sub">$subhead</div>
      <a class="cta">$cta</a>
    </div>
    <div class="brandmark"><b>$brand</b> &nbsp;·&nbsp; created by an AI agent workflow</div>
  </div>
</body></html>""")


def render_html(b: dict, bg_image: str | None = None, overlay: bool = False) -> str:
    w, h = ASPECTS[b["aspect"]]
    portrait = h >= w
    # type scale relative to the short edge so every aspect reads well
    base = min(w, h)
    fs_h1 = round(base * (0.095 if portrait else 0.085))
    fs_sub = round(base * 0.028)
    fs_cta = round(base * 0.03)
    fs_eye = round(base * 0.019)
    gap = round(base * 0.028)
    # per-word stagger for the headline reveal
    words = b["headline"].split()
    spans = "".join(
        f'<span class="w" style="animation-delay:{0.25 + i*0.07:.2f}s">{_esc(word)}</span> '
        for i, word in enumerate(words)
    )
    # three modes: overlay (transparent, for compositing over VIDEO) / still bg / type-only.
    if overlay:
        # transparent stage + scrim + text-shadow so type stays legible over moving footage.
        bg_url, bg_layer = "", '<div class="scrim"></div>'
        # html/body paint an OPAQUE --bg by default; must clear them too or the alpha capture is a
        # solid plate over the video. --default-background-color=00000000 only fills unpainted areas.
        overlay_css = ("html,body{background:transparent!important}"
                       ".stage{background:transparent!important}.stage::before{display:none}"
                       "h1,.sub,.eyebrow,.brandmark{text-shadow:0 2px 14px rgba(0,0,0,.6)}")
    elif bg_image:
        # embed the still as a data URI so headless Chrome always loads it (no file:// flags)
        import base64
        b64 = base64.b64encode(Path(bg_image).read_bytes()).decode()
        bg_url = f"data:image/png;base64,{b64}"
        bg_layer, overlay_css = '<div class="bg"></div><div class="scrim"></div>', ""
    else:
        bg_url, bg_layer, overlay_css = "", "", ""
    return _PAGE.substitute(
        w=w, h=h, gap=gap, dur=b["duration"],
        fs_h1=fs_h1, fs_sub=fs_sub, fs_cta=fs_cta, fs_eye=fs_eye,
        accent=b["brand"]["accent"], bg=b["brand"]["bg"], fg=b["brand"]["fg"],
        eyebrow=_esc(b["eyebrow"]), headline_html=spans.strip(),
        subhead=_esc(b["subhead"]), cta=_esc(b["cta"]), brand=_esc(b["brand"]["name"]),
        bg_url=bg_url, bg_layer=bg_layer, overlay_css=overlay_css,
    )


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ============================================================================
# RENDER: html -> frames -> mp4
# ============================================================================

def _capture_frames(html_path, frames, w, h, n, fps, transparent=False, ck=None):
    """Virtual-time capture: one PNG per frame, animation frozen at t=i/fps."""
    for old in frames.glob("frame_*.png"):
        old.unlink()
    for i in range(n):
        if ck:
            ck()
        t_ms = max(100, int(round(i / fps * 1000)))
        shot.capture(html_path, frames / f"frame_{i:04d}.png", width=w, height=h,
                     wait=t_ms, transparent=transparent)


def _run_ffmpeg(cmd, mp4, what):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not Path(mp4).exists():
        raise RuntimeError(f"ffmpeg {what} failed:\n{r.stderr[-1500:]}")


def make_ad(b: dict, outdir="out", on_status=None, should_cancel=None) -> str:
    """Render brief `b` to an mp4. Returns the mp4 path. Raises with a clear message on failure.

    Routes on b['visual_model']: 'wan' = motion b-roll under the type layer (ffmpeg alpha overlay);
    'z-image'/'krea'/'illustrious' = AI still behind the type layer; None = type layer only.
    on_status(msg) — optional coarse-progress callback. should_cancel() truthy -> abort (render.Cancelled).
    """
    say = on_status or (lambda _m: None)

    def ck():
        if should_cancel and should_cancel():
            import render as _r
            _r.interrupt()
            raise _r.Cancelled()
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH")
    if not shot.find_browser():
        raise RuntimeError("no headless Chrome/Edge found (set SHOT_BROWSER)")

    w, h = ASPECTS[b["aspect"]]
    fps = b["fps"]
    slug = _slug(b["brand"]["name"] + "-" + b["headline"])
    out = Path(outdir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    frames = out / (slug + "_frames")
    frames.mkdir(parents=True, exist_ok=True)
    html_path = out / (slug + ".html")
    mp4 = out / (slug + ".mp4")
    n = max(1, int(round(b["duration"] * fps)))
    vm = b.get("visual_model")

    # ---- Rung 3: MOTION b-roll under a transparent type layer (ffmpeg composites) ----
    if vm == "wan":
        import render
        say("Rendering AI video b-roll on the GPU (~4 min)…")
        broll = render.render_broll(b, outdir, should_cancel=should_cancel)
        say("Compositing the type layer over the video…")
        html_path.write_text(render_html(b, overlay=True), encoding="utf-8")
        _capture_frames(html_path, frames, w, h, n, fps, transparent=True, ck=ck)
        # scale/crop b-roll to canvas, overlay the RGBA type frames; fps on BOTH inputs (no judder)
        fc = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps},"
              f"setsar=1[bg];[1:v]fps={fps}[ov];[bg][ov]overlay=0:0:format=auto:shortest=1[out]")
        _run_ffmpeg(["ffmpeg", "-y", "-i", str(broll), "-framerate", str(fps),
                     "-i", str(frames / "frame_%04d.png"), "-filter_complex", fc,
                     "-map", "[out]", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                     "-movflags", "+faststart", str(mp4)], mp4, "overlay")
        shutil.rmtree(frames, ignore_errors=True)   # frames are throwaway once the mp4 exists
        return str(mp4)

    # ---- Rung 2 (AI still behind type) / Rung 1 (type only) ----
    bg = None
    if vm:
        import render
        say("Rendering the AI image on the GPU…")
        bg = render.render_still(b, outdir, should_cancel=should_cancel)
        if b.get("verify", True):
            ok, note = render.verify_still(bg, b)
            print(("  visual verify OK: " if ok else "  visual verify WARN: ") + note)

    say("Animating the type layer…")
    html_path.write_text(render_html(b, bg_image=bg), encoding="utf-8")
    _capture_frames(html_path, frames, w, h, n, fps, transparent=False, ck=ck)
    _run_ffmpeg(["ffmpeg", "-y", "-framerate", str(fps), "-i", str(frames / "frame_%04d.png"),
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)],
                mp4, "stitch")
    shutil.rmtree(frames, ignore_errors=True)   # frames are throwaway once the mp4 exists
    return str(mp4)


# ============================================================================
# CLI + self-check
# ============================================================================

def _selftest():
    """No browser/ffmpeg needed: brief validates and HTML renders with the copy embedded."""
    b = demo_brief()
    html = render_html(b)
    assert b["headline"] in " ".join(  # words are span-split, so check each survives
        w for w in b["headline"].split() if w in html) or all(w in html for w in b["headline"].split())
    assert b["cta"] in html and b["brand"]["accent"] in html
    assert f'{ASPECTS[b["aspect"]][0]}px' in html
    # a bad angle must be rejected
    try:
        new_brief(angle="Not An Angle"); raise SystemExit("SELFTEST FAIL: bad angle accepted")
    except ValueError:
        pass
    print("selftest ok")


def _main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: python ad.py demo | selftest | <brief.json> [outdir]")
        return 0
    if argv[0] == "selftest":
        _selftest(); return 0
    if argv[0] == "demo":
        b = demo_brief()
    else:
        b = new_brief(**json.loads(Path(argv[0]).read_text(encoding="utf-8")))
    outdir = argv[1] if len(argv) > 1 else "out"
    print("rendering:", b["headline"], f'({b["aspect"]}, {b["duration"]}s @ {b["fps"]}fps)')
    print("->", make_ad(b, outdir))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
