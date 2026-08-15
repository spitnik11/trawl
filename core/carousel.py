"""carousel.py — Trawl carousel spec -> a LinkedIn-ready multi-slide PDF ("document post").

LinkedIn's 2026 algorithm favours carousel / document posts (15-25% engagement) over single
images. This turns a slide spec into a swipeable PDF you upload as a document post.

Design law (user, 2026-08-05): slides must be BIG and READABLE — huge bold headlines, high
contrast, generous whitespace, one idea per slide, short lines. Modelled on the clean editorial
"cover + contents + footer" look (Notion/Bolis style). Light theme by default; dark available.

Stdlib only. The one runtime dep is the same headless Chrome/Edge shot.py already uses — we drive
its `--print-to-pdf` so the text stays **vector-crisp**, with zero PDF encoding by hand and no pip
installs. Fully additive: a new module + a new `carousel` CLI verb; nothing else in Trawl changes.

    from carousel import make_carousel, demo_spec
    make_carousel(demo_spec(), "out")          # -> out/<slug>.pdf
    python trawl.py carousel [spec.json]        # wired into the trawl CLI

Spec shape (all optional except title/slides; keep headings SHORT so they render BIG):
    {
      "handle": "Gabriel Pina",
      "theme": "light",                  # "light" (default) | "dark"
      "pill": "PROPER AI USE",           # small tag on the cover
      "footer": "AUGUST 2026",           # bottom-left meta on every slide
      "url": "portfolio-abl.pages.dev",  # bottom-right on every slide
      "title": "The cover headline",
      "subtitle": "one line under it",
      "inside": ["what's on slide 2", "slide 3", ...],   # cover contents list
      "slides": [ {"kicker": "01", "heading": "SHORT", "body": "one or two short lines"}, ... ],
      "cta": {"heading": "Your close", "body": "Follow / DM me 'BUILD'."}
    }

ponytail: browser print-to-pdf, not a hand-rolled PDF writer.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from string import Template

import shot  # sibling module: browser discovery + the throwaway-profile pattern

# LinkedIn carousels read best portrait 4:5. Even, high-res.
ASPECTS = {"4:5": (1080, 1350), "1:1": (1080, 1080), "9:16": (1080, 1920)}
DEFAULT_ASPECT = "4:5"

# Themes — high contrast on purpose. accent = a readable lime that ties to the brand.
THEMES = {
    "light": {"bg": "#f4f0e6", "ink": "#17160f", "mut": "#6f6c61", "line": "#d9d3c5", "accent": "#8faa12"},
    "dark":  {"bg": "#0e1013", "ink": "#f4f2ec", "mut": "#a7a49c", "line": "#2a2d2b", "accent": "#c6f622"},
}


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in str(s).lower()).strip("-")[:48] or "carousel"


# BIG + readable: heavy system sans headlines, mono meta, huge type, generous margins.
_DOC = Template("""<!doctype html><html><head><meta charset="utf-8"><style>
  @page { size: ${w}px ${h}px; margin: 0; }
  :root{ --bg:${bg}; --ink:${ink}; --mut:${mut}; --line:${line}; --accent:${accent};
    --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,Roboto,"Helvetica Neue",Arial,sans-serif;
    --mono:ui-monospace,"Cascadia Mono",Consolas,Menlo,monospace; }
  *{ margin:0; box-sizing:border-box; -webkit-print-color-adjust:exact; print-color-adjust:exact; }
  html,body{ background:var(--bg); }
  .slide{ position:relative; width:${w}px; height:${h}px; overflow:hidden; background:var(--bg);
    color:var(--ink); font-family:var(--sans); padding:${pad}px; display:flex; flex-direction:column;
    break-after:page; page-break-after:always; }
  .slide:last-child{ break-after:auto; page-break-after:auto; }
  .top,.bottom{ display:flex; align-items:center; justify-content:space-between; }
  .brand{ font-weight:800; font-size:36px; letter-spacing:-.01em; display:flex; align-items:center; gap:16px; }
  .brand .dot{ width:26px; height:26px; border-radius:8px; background:var(--accent); }
  .pill{ font-family:var(--mono); font-size:22px; letter-spacing:.14em; text-transform:uppercase;
    color:var(--mut); border:2px solid var(--line); border-radius:999px; padding:11px 22px;
    display:flex; align-items:center; gap:13px; }
  .pill .d{ width:12px; height:12px; border-radius:99px; background:var(--accent); }
  .page{ font-family:var(--mono); font-size:24px; letter-spacing:.12em; color:var(--mut); }
  .mid{ flex:1; display:flex; flex-direction:column; justify-content:center; }
  .kicker{ font-family:var(--mono); font-size:26px; letter-spacing:.2em; text-transform:uppercase;
    color:var(--accent); margin-bottom:38px; }
  h1{ font-weight:800; font-size:124px; line-height:.96; letter-spacing:-.04em; }
  h2{ font-weight:800; font-size:88px; line-height:1.0; letter-spacing:-.035em; max-width:15ch; }
  .sub{ font-size:40px; line-height:1.35; color:var(--mut); margin-top:44px; max-width:22ch; }
  .body{ font-size:46px; font-weight:500; line-height:1.28; margin-top:44px; max-width:19ch; }
  .inside{ margin-top:52px; font-size:30px; line-height:1.85; color:var(--mut); }
  .inside .n{ font-family:var(--mono); color:var(--accent); margin-right:14px; }
  .bottom{ font-family:var(--mono); font-size:23px; letter-spacing:.06em; color:var(--mut); text-transform:uppercase; }
  .bottom b{ color:var(--ink); font-weight:700; }
  .cta h2{ color:var(--accent); }
</style></head><body>
${slides}
</body></html>""")

_COVER = Template("""<section class="slide">
  <div class="top"><div class="brand"><span class="dot"></span>${handle}</div>${pill}</div>
  <div class="mid">
    ${kicker}
    <h1>${title}</h1>
    ${subtitle}
    ${inside}
  </div>
  <div class="bottom"><span>${footer}</span><span>${url}</span></div>
</section>""")

_SLIDE = Template("""<section class="slide">
  <div class="top"><div class="brand"><span class="dot"></span>${handle}</div><span class="page">${page}</span></div>
  <div class="mid">
    ${kicker}
    <h2>${heading}</h2>
    ${body}
  </div>
  <div class="bottom"><span>${footer}</span><span>${url}</span></div>
</section>""")

_CTA = Template("""<section class="slide cta">
  <div class="top"><div class="brand"><span class="dot"></span>${handle}</div>${pill}</div>
  <div class="mid">
    <h2>${heading}</h2>
    ${body}
  </div>
  <div class="bottom"><span>${footer}</span><span>${url}</span></div>
</section>""")


def _pill(text):
    return f'<div class="pill"><span class="d"></span>{_esc(text)}</div>' if text else "<span></span>"


def render_html(spec: dict) -> str:
    aspect = spec.get("aspect", DEFAULT_ASPECT)
    w, h = ASPECTS.get(aspect, ASPECTS[DEFAULT_ASPECT])
    t = THEMES.get(spec.get("theme", "light"), THEMES["light"])
    pad = round(w * 0.093)
    handle = _esc(spec.get("handle", ""))
    footer = _esc(spec.get("footer", ""))
    url = _esc(spec.get("url", ""))
    slides = spec.get("slides", [])
    total = len(slides) + 2  # cover + slides + cta

    inside = ""
    if spec.get("inside"):
        rows = "".join(f'<div><span class="n">{i:02d}</span>{_esc(x)}</div>'
                       for i, x in enumerate(spec["inside"], start=1))
        inside = f'<div class="inside">{rows}</div>'

    parts = [_COVER.substitute(
        handle=handle, pill=_pill(spec.get("pill")),
        kicker=(f'<div class="kicker">{_esc(spec["kicker"])}</div>' if spec.get("kicker") else ""),
        title=_esc(spec.get("title", "")),
        subtitle=(f'<div class="sub">{_esc(spec["subtitle"])}</div>' if spec.get("subtitle") else ""),
        inside=inside, footer=footer, url=url,
    )]
    for i, sl in enumerate(slides, start=1):
        parts.append(_SLIDE.substitute(
            handle=handle, page=f"{i+1:02d} / {total:02d}",
            kicker=(f'<div class="kicker">{_esc(sl.get("kicker") or f"{i:02d}")}</div>'),
            heading=_esc(sl.get("heading", "")),
            body=(f'<div class="body">{_esc(sl["body"])}</div>' if sl.get("body") else ""),
            footer=footer, url=url,
        ))
    cta = spec.get("cta")
    if cta:
        parts.append(_CTA.substitute(
            handle=handle, pill=_pill(cta.get("pill", "Your move")),
            heading=_esc(cta.get("heading", "")),
            body=(f'<div class="body">{_esc(cta["body"])}</div>' if cta.get("body") else ""),
            footer=footer, url=url,
        ))
    return _DOC.substitute(w=w, h=h, pad=pad, slides="\n".join(parts), **t)


def _print_pdf(html_path: Path, pdf_path: Path, timeout: int = 60) -> None:
    browser = shot.find_browser()
    if not browser:
        raise RuntimeError("No Edge/Chrome found. Install one, or set SHOT_BROWSER to its .exe path.")
    if pdf_path.exists():
        pdf_path.unlink()  # a stale file must not masquerade as success
    import os
    profile = Path(os.environ.get("TEMP", ".")) / "carousel-profile"
    args = [browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
            f"--user-data-dir={profile}", f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()]
    try:
        subprocess.run(args, timeout=timeout, capture_output=True)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Browser timed out after {timeout}s printing {html_path}")
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise RuntimeError(f"No PDF produced for {html_path} (empty or missing {pdf_path})")


def make_carousel(spec: dict, outdir="out") -> str:
    """Render a carousel spec to a LinkedIn-ready PDF. Returns the pdf path."""
    out = Path(outdir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    slug = _slug(spec.get("title", "carousel"))
    html_path = out / f"{slug}.html"
    pdf_path = out / f"{slug}.pdf"
    html_path.write_text(render_html(spec), encoding="utf-8")
    _print_pdf(html_path, pdf_path)
    return str(pdf_path)


def demo_spec() -> dict:
    return {
        "handle": "Gabriel Pina",
        "theme": "light",
        "pill": "Proper AI use",
        "footer": "August 2026",
        "url": "portfolio-abl.pages.dev",
        "title": "Your agent's real bottleneck isn't the model.",
        "subtitle": "It's the workflow around the prompt.",
        "inside": ["Why long agent runs stall", "The limits you're hitting",
                   "The local-model hedge", "Route by difficulty"],
        "slides": [
            {"heading": "An agent is a model in a loop.",
             "body": "Plan, call a tool, check, repeat — dozens of API calls per task."},
            {"heading": "That's what hosted APIs throttle.",
             "body": "Requests per minute, tokens per minute, daily caps. Cross one and the run stalls."},
            {"heading": "Backoff just makes you wait.",
             "body": "You pay in latency to sit in your own queue. The ceiling is still there."},
            {"heading": "Make the big model a specialist.",
             "body": "Route the routine steps to a local model. Keep the frontier API for the hard reasoning."},
            {"heading": "Route by difficulty, not by habit.",
             "body": "Most steps aren't hard reasoning — and never need to leave your machine."},
        ],
        "cta": {"heading": "That clarity is the skill.",
                "body": "Using AI well is workflow design, not a cleverer prompt. Follow for more, or DM me 'BUILD'."},
    }


def _selftest():
    """No browser needed: the HTML carries every slide, is print-sized, and prints backgrounds."""
    spec = demo_spec()
    html = render_html(spec)
    assert html.count('<section class="slide') == len(spec["slides"]) + 2
    assert "1080px 1350px" in html and "print-color-adjust:exact" in html
    assert THEMES["light"]["bg"] in html and spec["title"] in html
    assert "02 / 07" in html  # page counter on the first content slide
    print("selftest ok")


def _main(argv):
    if argv and argv[0] == "selftest":
        _selftest(); return 0
    if not argv or argv[0] == "demo":
        spec = demo_spec()
    else:
        spec = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    outdir = argv[1] if len(argv) > 1 else "out"
    print("->", make_carousel(spec, outdir))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
