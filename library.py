#!/usr/bin/env python3
"""Post library — a notes index over every post built so far.

NOT a copy machine. A compact, always-current digest of what's already been made — artifacts and
angles covered, the caption voice that worked, poster design + reusable POC components, and the
approval signal — so future posts are faster to generate/build and proofs of concept can lift
proven patterns instead of starting cold.

Source of truth stays the post folders themselves; this only indexes them, so it can't drift.
Consumers:
  - Claude, when processing the post queue (every build prompt points here).
  - the app, via GET /api/library.
  - you, via `python trawl.py library` -> writes Posts\\LIBRARY.md + library.json.

Built on top of board.read_posts() so parsing lives in one place. Stdlib only.
"""
import json
import re
from pathlib import Path

import board

# Family tokens, not the bare words "serif"/"sans" — "sans-serif" would otherwise tag every
# poster as serif. Heuristic on purpose: this is design *notes*, not a spec.
FONT_KINDS = (
    ("serif", re.compile(r"georgia|garamond|palatino|charter|cambria|times|ui-serif", re.I)),
    ("mono", re.compile(r"consolas|menlo|cascadia|courier|ui-monospace|sf ?mono|\bmono\b", re.I)),
    ("sans", re.compile(r"system-ui|-apple-system|segoe|roboto|helvetica|arial|inter|ui-sans", re.I)),
)


def poc_notes(html):
    """Heuristic design/structure fingerprint of a poc.html — what pattern it is, for reuse."""
    low = html.lower()
    fonts = sorted({kind for kind, pat in FONT_KINDS if pat.search(html)})
    accents = re.findall(r"--accent[\w-]*:\s*(#[0-9a-fA-F]{3,8})", html)
    structs = []
    if "<table" in low: structs.append("table")
    if "<svg" in low: structs.append("svg")
    if "grid-template" in low: structs.append("grid")
    if "<iframe" in low: structs.append("iframe")
    return {
        "fonts": fonts or ["sans"],
        "accent": accents[0] if accents else "",
        "structures": structs,
        "themed": ("prefers-color-scheme" in low) or ("data-theme" in low),
        "bytes": len(html),
    }


def build_library(posts_dir):
    """Digest every post folder into a structured library dict."""
    board.configure(posts_dir)
    posts = board.read_posts()
    approvals = board.load_approvals()
    root = Path(posts_dir)
    entries = []
    for p in posts:
        a = approvals.get(p["slug"], {})
        pocf = root / p["slug"] / "poc.html"
        poc = poc_notes(pocf.read_text(encoding="utf-8", errors="ignore")) if pocf.exists() else {}
        body = (p.get("body") or "").strip()
        entries.append({
            "slug": p["slug"], "artifact": p["artifact"], "category": p["category"],
            "style": p["style"], "status": p["status"], "keyword": p["keyword"],
            "hook": p["hook"],
            "caption_opener": body.splitlines()[0] if body else "",
            "approved": a.get("approved"), "approval_note": a.get("note", ""),
            "poc": poc,
        })
    entries.sort(key=lambda e: e["slug"])

    def tally(key):
        d = {}
        for e in entries:
            d[e[key] or "—"] = d.get(e[key] or "—", 0) + 1
        return dict(sorted(d.items(), key=lambda kv: -kv[1]))

    return {
        "count": len(entries),
        "artifacts": sorted({e["artifact"] for e in entries}),
        "by_category": tally("category"),
        "by_style": tally("style"),
        "posts": entries,
    }


def render_md(lib):
    """Compact, human/Claude-readable LIBRARY.md. Pointers to detail, not full captions."""
    L = ["# Post Library — auto-generated (`python trawl.py library`)", ""]
    L.append(f"A notes index over **{lib['count']} posts** built so far. Use it to write future "
             "posts faster, keep the voice consistent, and lift proven POC patterns. "
             "**Reference the patterns — never copy a past post.**")
    L += ["", "## Coverage (avoid repeating the same angle)",
          "- **Artifacts:** " + (", ".join(lib["artifacts"]) or "—"),
          "- **By category:** " + ", ".join(f"{k} ({v})" for k, v in lib["by_category"].items()),
          "- **By style:** " + ", ".join(f"{k} ({v})" for k, v in lib["by_style"].items()),
          "", "## Posts"]
    for e in lib["posts"]:
        appr = {True: "approved", False: "rejected"}.get(e["approved"], "unreviewed")
        L.append(f"### {e['slug']} — {e['artifact']}  ·  {e['style']} · {e['status']} · {appr}")
        if e["hook"]:
            L.append(f"- Hook: {e['hook']}")
        if e["caption_opener"] and e["caption_opener"] != e["hook"]:
            L.append(f"- Caption opens: {e['caption_opener']}")
        poc = e["poc"]
        if poc:
            sig = "/".join(poc["fonts"])
            if poc.get("accent"):
                sig += f", accent {poc['accent']}"
            if poc.get("structures"):
                sig += f", {'+'.join(poc['structures'])}"
            if poc.get("themed"):
                sig += ", light+dark"
            L.append(f"- Poster: {sig} → `{e['slug']}/poc.html`")
        if e["approval_note"]:
            L.append(f"- Review note: {e['approval_note']}")
        L.append("")
    L += ["## Reusable POC components",
          "Need a proof of concept? Open the file and lift the *pattern*, not the content:"]
    for e in lib["posts"]:
        poc = e["poc"]
        if poc and poc.get("structures"):
            L.append(f"- **{'+'.join(poc['structures'])}** "
                     f"({'/'.join(poc['fonts'])}{', ' + poc['accent'] if poc.get('accent') else ''}) "
                     f"— `{e['slug']}/poc.html`")
    L.append("")
    return "\n".join(L)


def refresh(posts_dir):
    """Rebuild LIBRARY.md + library.json in the posts dir. Returns the library dict."""
    lib = build_library(posts_dir)
    root = Path(posts_dir)
    (root / "LIBRARY.md").write_text(render_md(lib), encoding="utf-8")
    (root / "library.json").write_text(json.dumps(lib, indent=2), encoding="utf-8")
    return lib


def selfcheck(posts_dir):
    lib = build_library(posts_dir)
    assert lib["count"] >= 1 and lib["posts"], "no posts indexed"
    for e in lib["posts"]:
        assert e["slug"] and isinstance(e["poc"], dict)
    md = render_md(lib)
    assert md.startswith("# Post Library") and "never copy" in md
    reusable = sum(1 for e in lib["posts"] if e["poc"].get("structures"))
    print(f"ok — {lib['count']} posts indexed; {reusable} with reusable POC patterns")


if __name__ == "__main__":
    import sys
    selfcheck(sys.argv[1] if len(sys.argv) > 1
              else r"C:\Users\losth\Documents\ClaudeBrain\02 Projects\AI-Beginner-Business\Posts")
