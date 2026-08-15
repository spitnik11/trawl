#!/usr/bin/env python3
"""Post review board — the front door of the combined Trawl app.

Relocated from the old standalone `Posts\\review.py`. It no longer runs its own HTTP server;
trawl.py serves its PAGE at `/` and calls these helpers. The post DATA still lives in the Ship
Something Real `Posts\\` folder — pointed at by `posts_dir` in config.json and set via configure().

Two kinds of state, kept separate on purpose:
- `approvals.json` — SOFT signal (approve / not-this + a note). Steers the generator; not a gate.
- `POSTING-LOG.md` — SOURCE OF TRUTH for what's actually posted. "Mark posted" rewrites its row.

Posting is assisted, not autonomous: the buttons copy the right text and open the composer +
poster. A human still hits send (X/LinkedIn have no free post API and automating a logged-in
session risks the account).

Stdlib only.
"""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

# Set by trawl.py at startup from config["posts_dir"]. Everything below reads/writes under it.
POSTS_DIR = None

# PNG export layout (additive — never renames/deletes poc.html or post.md):
#   Posts/<slug>/poster.png     canonical screenshot next to the source HTML
#   Posts/_exports/<slug>.png   central mirror (hardlink when possible, else copy)
POSTER_NAME = "poster.png"
EXPORTS_NAME = "_exports"
# 4:5 social-friendly frame; tall enough for most single-card posters.
POSTER_W, POSTER_H = 1080, 1350
POSTER_WAIT_MS = 2000


def configure(posts_dir):
    global POSTS_DIR
    POSTS_DIR = Path(posts_dir)
    return POSTS_DIR


def _valid_slug(slug):
    return bool(re.fullmatch(r"[\w-]+", slug or ""))


def _approvals_path():
    return POSTS_DIR / "approvals.json"


def _log_path():
    return POSTS_DIR / "POSTING-LOG.md"


def _req_dir():
    return POSTS_DIR / "requests"


def load_approvals():
    p = _approvals_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_approvals(d):
    _approvals_path().write_text(json.dumps(d, indent=2), encoding="utf-8")


def read_log():
    """Parse the POSTING-LOG table -> {slug: {status, platform, date}}. The table is the truth."""
    out = {}
    log = _log_path()
    if not log.exists():
        return out
    for line in log.read_text(encoding="utf-8").splitlines():
        m = re.search(r"\[([\w-]+)\]", line)
        if not line.startswith("|") or not m:
            continue
        cells = [c.strip() for c in line.split("|")]
        # cells: ['', link, artifact, category, keyword, status, platform, date, '']
        if len(cells) >= 8:
            out[m.group(1)] = {"status": cells[5], "platform": cells[6], "date": cells[7]}
    return out


def mark_posted(slug, platform):
    """Rewrite the slug's row: Status->posted, Platform, Date. POSTING-LOG stays the truth."""
    log = _log_path()
    if not log.exists():
        return False
    lines = log.read_text(encoding="utf-8").splitlines()
    today = time.strftime("%Y-%m-%d")
    changed = False
    for i, line in enumerate(lines):
        if line.startswith("|") and re.search(rf"\[{re.escape(slug)}\]", line):
            cells = line.split("|")
            if len(cells) >= 8:
                cells[5] = " posted "
                cells[6] = f" {platform} "
                cells[7] = f" {today} "
                lines[i] = "|".join(cells)
                changed = True
            break
    if changed:
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed


def _section(text, *keys):
    """Return the prose under the first `## <heading containing a key>`, quote markers stripped."""
    for block in re.split(r"^##\s+", text, flags=re.M)[1:]:
        title = block.splitlines()[0].lower()
        if any(k in title for k in keys):
            body = "\n".join(block.splitlines()[1:])
            return re.sub(r"^\s*>\s?", "", body, flags=re.M).strip()
    return ""


def read_posts():
    log = read_log()
    posts = []
    for folder in POSTS_DIR.iterdir():
        md, poc, vid = folder / "post.md", folder / "poc.html", folder / "ad.mp4"
        if not (folder.is_dir() and md.exists()):
            continue
        text = md.read_text(encoding="utf-8")
        # [ \t]* not \s*: \s matches newlines, so an empty value (e.g. `keyword:`) would swallow
        # the next front-matter line as its value.
        fm = dict(re.findall(r"^(\w+):[ \t]*(.*)$", text.split("---")[1], re.M)) if "---" in text else {}
        x_body = _section(text, "tweet", "twitter", "x /") or ""
        li_body = _section(text, "linkedin") or x_body
        ad_body = _section(text, "caption", "showcase") or ""
        m = re.search(r"## Hook.*?\n>\s*(.+)", text, re.S)
        hook = m.group(1).splitlines()[0].strip() if m else (x_body.splitlines()[0] if x_body else "")
        style = (fm.get("style") or "").lower()
        # The caption that ships with the screenshot, in the post's chosen platform voice.
        body = (x_body if style in ("twitter", "x") else li_body if style == "linkedin"
                else ad_body or li_body or x_body if style in ("ad", "poc")
                else li_body or x_body or hook)
        lg = log.get(folder.name, {})
        poster = folder / POSTER_NAME
        posts.append({
            "slug": folder.name,
            "artifact": fm.get("artifact", folder.name),
            "category": fm.get("category", ""),
            "keyword": fm.get("keyword", ""),
            "format": fm.get("format", ""),
            "style": style or "linkedin",
            "body": body,
            "status": lg.get("status", fm.get("status", "ready")),
            "platform": lg.get("platform", "—"),
            "date": lg.get("date", "—"),
            "hook": hook,
            "x_body": x_body,
            "li_body": li_body,
            "has_poc": poc.exists(),
            "has_video": vid.exists(),
            "has_poster": poster.exists() and poster.stat().st_size > 0,
            "mtime": folder.stat().st_mtime,
        })
    return posts


def read_requests():
    reqs = []
    rd = _req_dir()
    if rd.exists():
        for f in sorted(rd.glob("*.json")):
            try:
                reqs.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
    return reqs


def poc_bytes(slug):
    """Return the bytes of a post's poc.html, or None. `slug` is validated before we touch disk."""
    if not _valid_slug(slug):
        return None
    f = POSTS_DIR / slug / "poc.html"
    return f.read_bytes() if f.exists() else None


def video_bytes(slug):
    """Return the bytes of a post's ad.mp4, or None. `slug` is validated (path-traversal guard)."""
    if not _valid_slug(slug):
        return None
    f = POSTS_DIR / slug / "ad.mp4"
    return f.read_bytes() if f.exists() else None


def poster_path(slug):
    """Path to Posts/<slug>/poster.png, or None if slug is invalid."""
    if not _valid_slug(slug):
        return None
    return POSTS_DIR / slug / POSTER_NAME


def poster_bytes(slug):
    """Return the bytes of a post's poster.png, or None."""
    p = poster_path(slug)
    return p.read_bytes() if p is not None and p.exists() else None


def exports_dir():
    """Central folder of exported PNGs (created on demand). Safe to open in Explorer."""
    d = POSTS_DIR / EXPORTS_NAME
    d.mkdir(exist_ok=True)
    return d


def _mirror_export(src, slug):
    """Mirror src into Posts/_exports/<slug>.png (hardlink when free, else copy)."""
    dest = exports_dir() / f"{slug}.png"
    if dest.exists() or dest.is_symlink():
        try:
            dest.unlink()
        except OSError:
            pass
    try:
        os.link(src, dest)  # same-volume NTFS hardlink — no double disk use
    except OSError:
        shutil.copy2(src, dest)
    return dest


def ensure_poster(slug, force=False, width=POSTER_W, height=POSTER_H, wait=POSTER_WAIT_MS):
    """Render poc.html → poster.png via shot.py; mirror into _exports/.

    Additive only: never touches poc.html / post.md / ad.mp4. Returns a dict with
    path, export, bytes, cached. Raises ValueError for bad/missing input, RuntimeError
    if the browser capture fails.
    """
    import shot  # lazy: keeps board import light; shot is stdlib-only, no trawl deps

    if not _valid_slug(slug):
        raise ValueError("invalid slug")
    html = POSTS_DIR / slug / "poc.html"
    if not html.is_file():
        raise ValueError("no poc.html for this post (video-only posts have no HTML poster)")
    out = POSTS_DIR / slug / POSTER_NAME
    cached = out.is_file() and out.stat().st_size > 0 and not force
    if not cached:
        shot.capture(html, out, width=width, height=height, wait=wait)
    exp = _mirror_export(out, slug)
    return {
        "slug": slug,
        "path": str(out),
        "export": str(exp),
        "bytes": out.stat().st_size,
        "cached": cached,
    }


def open_folder(slug=None, exports=False):
    """Open Explorer on a post folder (PNG selected if present) or on Posts/_exports/.

    Returns True if the OS call was issued. Path-traversal / missing targets → False.
    """
    if exports or not slug:
        return _reveal(exports_dir(), select=False)
    if not _valid_slug(slug):
        return False
    folder = POSTS_DIR / slug
    if not folder.is_dir():
        return False
    for name in (POSTER_NAME, "ad.mp4", "poc.html", "post.md"):
        cand = folder / name
        if cand.is_file():
            return _reveal(cand, select=True)
    return _reveal(folder, select=False)


def _reveal(path, select=False):
    """Windows: explorer /select or startfile. Other OS: xdg-open the folder."""
    path = Path(path).resolve()
    if os.name == "nt":
        if select and path.is_file():
            subprocess.Popen(["explorer", f"/select,{path}"])
            return True
        if path.is_dir():
            os.startfile(str(path))  # local trusted path only
            return True
        if path.is_file():
            subprocess.Popen(["explorer", f"/select,{path}"])
            return True
        return False
    folder = path if path.is_dir() else path.parent
    try:
        subprocess.Popen(["xdg-open", str(folder)])
        return True
    except OSError:
        return False


def queue_specs(ptype, specs):
    """Write generated specs as pending request files for Claude to build. Returns count queued."""
    rd = _req_dir()
    rd.mkdir(exist_ok=True)
    queued = 0
    for i, s in enumerate(specs):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^a-z0-9]+", "-", (s.get("artifact") or "post").lower()).strip("-")[:36]
        rid = f"{stamp}-{i}-{slug}-{ptype}"
        rec = {
            "id": rid, "artifact": s.get("artifact", ""), "category": s.get("category", ""),
            "keyword": s.get("keyword", ""), "style": s.get("style", ptype),
            "post_type": ptype, "kind": s.get("kind", "business"),
            "hook": s.get("hook", ""), "note": s.get("note", ""), "prompt": s.get("prompt", ""),
            "status": "pending", "created": stamp,
        }
        (rd / f"{rid}.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
        queued += 1
    return queued


def selfcheck(posts_dir):
    configure(posts_dir)
    posts = read_posts()
    assert isinstance(posts, list)
    for p in posts:
        assert p["slug"] and "hook" in p and "mtime" in p, p
        assert "has_poster" in p
    assert mark_posted("__nope__", "x") is False          # unknown slug must not raise or change
    assert poc_bytes("../etc") is None                     # path traversal rejected
    assert poster_bytes("../etc") is None
    assert open_folder("../etc") is False
    # exports dir is creatable and does not look like a post (no post.md)
    d = exports_dir()
    assert d.is_dir() and d.name == EXPORTS_NAME
    print(f"ok — {len(posts)} posts parsed from {POSTS_DIR}; log truth respected")


FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'"
           "%3E%3Crect width='64' height='64' rx='10' fill='%23141a1f'/%3E"
           "%3Crect x='13' y='17' width='38' height='8' rx='3' fill='%232b6cb0'/%3E"
           "%3Crect x='13' y='28' width='38' height='8' rx='3' fill='%234a86c6'/%3E"
           "%3Crect x='13' y='39' width='28' height='8' rx='3' fill='%237fb0de'/%3E%3C/svg%3E")

PAGE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Trawl</title>
<link rel="icon" href="__FAVICON__">
<style>
 :root{--bg:#f4f4f2;--panel:#ffffff;--line:#e0e0db;--ink:#1a1a1d;--mut:#52525a;--sub:#767680;
   --field:#ececea;--accent:#1a1a1d;--accent-ink:#ffffff;--dot:rgba(0,0,0,.05);
   --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
   --mono:ui-monospace,"SF Mono","Cascadia Code",Consolas,Menlo,monospace}
 @media(prefers-color-scheme:dark){:root{--bg:#141417;--panel:#1d1d21;--line:#303036;--ink:#f1f1f4;
   --mut:#b8b8c1;--sub:#8c8c96;--field:#26262b;--accent:#f1f1f4;--accent-ink:#141417;--dot:rgba(255,255,255,.05)}}
 *{box-sizing:border-box}
 body{margin:0;color:var(--ink);font-family:var(--sans);font-size:17px;line-height:1.6;
   -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;
   background:radial-gradient(circle at 1px 1px,var(--dot) 1px,transparent 0) 0 0/24px 24px,var(--bg);
   background-attachment:fixed}
 header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);
   display:flex;align-items:center;gap:18px;padding:0 24px;height:64px}
 .brand{font-weight:800;letter-spacing:-.02em;font-size:22px}
 .tabs{display:flex;gap:3px;background:var(--field);border:1px solid var(--line);border-radius:11px;padding:4px}
 .tab{font:inherit;font-size:15px;font-weight:700;border:0;background:none;color:var(--mut);
   padding:8px 18px;border-radius:8px;cursor:pointer;transition:.12s}
 .tab.active{background:var(--panel);color:var(--ink);box-shadow:0 1px 3px rgba(0,0,0,.1)}
 @media(prefers-color-scheme:dark){.tab.active{box-shadow:0 1px 3px rgba(0,0,0,.55)}}
 .spacer{flex:1}
 .count{font-size:14px;font-weight:500;color:var(--sub)}
 .ghost{font:inherit;font-size:14px;font-weight:700;color:var(--mut);text-decoration:none;
   border:1px solid var(--line);background:none;border-radius:9px;padding:8px 14px;cursor:pointer}
 .ghost:hover{border-color:var(--sub);color:var(--ink)}
 main{max-width:840px;margin:0 auto;padding:34px 22px 110px}
 .view[hidden]{display:none}
 .panel{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:30px 30px 28px}
 .panel h2{font-size:31px;font-weight:800;letter-spacing:-.025em;line-height:1.08;margin:0}
 .hint{font-size:16px;color:var(--mut);margin:12px 0 28px;max-width:58ch}
 .fl{display:block;font-size:13px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;
   color:var(--sub);margin:0 0 12px}
 .fl .opt{text-transform:none;letter-spacing:0;font-weight:500}
 .segs{display:grid;grid-template-columns:repeat(auto-fit,minmax(232px,1fr));gap:12px;margin-bottom:26px}
 .seg{text-align:left;font:inherit;cursor:pointer;background:var(--panel);border:1.5px solid var(--line);
   border-radius:14px;padding:0;overflow:hidden;transition:.12s;display:block}
 .seg:hover{border-color:var(--sub);transform:translateY(-1px)}
 .seg .row{display:flex;align-items:center;gap:12px;padding:15px 16px 8px}
 .seg .ic{width:38px;height:38px;flex:none;display:grid;place-items:center;border:1.5px solid var(--line);
   border-radius:11px;color:var(--ink);transition:.12s}
 .seg .ic svg{width:20px;height:20px}
 .seg b{font-size:16.5px;font-weight:750;letter-spacing:-.01em}
 .seg>span{display:block;font-size:13.5px;color:var(--mut);line-height:1.45;padding:0 16px 15px}
 .seg.active{background:var(--accent);border-color:var(--accent)}
 .seg.active b{color:var(--accent-ink)}.seg.active>span{color:var(--accent-ink);opacity:.78}
 .seg.active .ic{border-color:transparent;background:rgba(140,140,150,.25);color:var(--accent-ink)}
 .kicker{font-family:var(--mono);font-size:12px;letter-spacing:.22em;text-transform:uppercase;
   color:var(--sub);margin:0 0 14px;display:flex;align-items:center;gap:10px}
 .kicker::before{content:"";width:24px;height:2px;background:var(--ink);border-radius:2px}
 .fl,.cnt label,.cat,.body .cap,.count,.kicker{font-family:var(--mono)}
 textarea{width:100%;font:inherit;font-size:16px;line-height:1.55;color:var(--ink);background:var(--bg);
   border:1.5px solid var(--line);border-radius:11px;padding:14px 16px;resize:vertical;margin-bottom:24px}
 textarea::placeholder{color:var(--sub)}
 textarea:focus{outline:none;border-color:var(--ink)}
 .genrow{display:flex;align-items:flex-end;gap:18px;flex-wrap:wrap}
 .cnt{display:flex;flex-direction:column;gap:9px}
 .cnt label{font-size:13px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--sub)}
 .cnt input{width:82px;font:inherit;font-size:19px;font-weight:700;text-align:center;padding:10px;
   border:1.5px solid var(--line);border-radius:10px;background:var(--bg);color:var(--ink)}
 .cnt input:focus{outline:none;border-color:var(--ink)}
 button.primary{font:inherit;font-size:16px;font-weight:750;background:var(--accent);color:var(--accent-ink);
   border:1.5px solid var(--accent);border-radius:10px;padding:13px 26px;cursor:pointer;transition:.12s;height:48px}
 button.primary:hover{opacity:.88}button.primary:active{transform:translateY(1px)}
 button.primary:disabled{opacity:.5;cursor:default}
 .revbar{display:flex;align-items:center;gap:12px;margin-bottom:24px}
 select{font:inherit;font-size:15px;font-weight:700;border:1.5px solid var(--line);background:var(--bg);
   color:var(--ink);border-radius:9px;padding:9px 14px;cursor:pointer}
 #list{display:flex;flex-direction:column;gap:26px}
 .empty{color:var(--sub);font-size:16px;text-align:center;padding:44px 0}
 .post{background:var(--panel);border:1px solid var(--line);border-radius:18px;overflow:hidden;position:relative}
 .post.approved{border-color:var(--ink);border-width:2px}
 .post.rejected{opacity:.5}.post.rejected:hover{opacity:1}
 .post.posted{opacity:.62}.post.posted:hover{opacity:1}
 .posted-badge{position:absolute;top:14px;right:14px;z-index:2;background:var(--accent);color:var(--accent-ink);
   font-size:12px;font-weight:800;letter-spacing:.09em;text-transform:uppercase;padding:6px 13px;border-radius:999px}
 .frame{border:0;width:100%;height:500px;background:var(--field);display:block;border-bottom:1px solid var(--line)}
 video.frame{object-fit:contain;background:#000}
 .meta{padding:20px 22px}
 .cat{font-size:12.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--sub);font-weight:800}
 .tag{display:inline-block;margin-left:9px;font-size:11.5px;padding:3px 9px;border-radius:999px;
   border:1px solid var(--line);color:var(--mut);letter-spacing:.05em;text-transform:uppercase;font-weight:700}
 .hook{font-size:21px;font-weight:700;margin:12px 0 5px;line-height:1.32;letter-spacing:-.015em}
 .sub{font-size:15px;color:var(--mut)}
 .body{margin:16px 0 2px;padding:16px 18px;background:var(--field);border:1px solid var(--line);
   border-radius:11px;font-size:16px;line-height:1.6;white-space:pre-wrap}
 .body .cap{font-size:11.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--sub);
   font-weight:800;margin-bottom:10px;display:flex;align-items:center;gap:8px}
 .body .copybtn{margin-left:auto;padding:5px 12px}
 .bar{display:flex;align-items:center;gap:9px;padding:16px 22px;border-top:1px solid var(--line);flex-wrap:wrap}
 .bar button{font:inherit;font-size:14.5px;font-weight:700;border:1.5px solid var(--line);background:var(--bg);
   color:var(--ink);border-radius:9px;padding:9px 15px;cursor:pointer;transition:.12s}
 .bar button:hover{border-color:var(--sub)}
 .bar button.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);height:auto;padding:9px 16px}
 .bar .sep{width:1px;height:22px;background:var(--line);margin:0 4px}
 .bar button.approve.on{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
 .bar button.reject.on{background:var(--field);border-color:var(--sub)}
 .bar button:disabled{opacity:.4;cursor:not-allowed}
 .png-thumb{width:42px;height:42px;object-fit:cover;border-radius:8px;border:1.5px solid var(--line);
   cursor:grab;vertical-align:middle;background:var(--field);flex:none}
 .png-thumb:active{cursor:grabbing}
 .state{font-size:13.5px;font-weight:600;color:var(--sub)}
 .note{flex:1 1 100%;margin-top:6px}
 .note input{width:100%;font:inherit;font-size:15px;padding:10px 12px;border:1.5px solid var(--line);
   border-radius:9px;background:var(--bg);color:var(--ink)}
 .note input::placeholder{color:var(--sub)}
 #toast{position:fixed;bottom:26px;left:50%;transform:translateX(-50%) translateY(20px);background:var(--ink);
   color:var(--bg);padding:12px 22px;border-radius:10px;font-size:15px;font-weight:600;opacity:0;transition:.2s;pointer-events:none;z-index:10}
 @media(prefers-color-scheme:dark){#toast{background:var(--panel);color:var(--ink);border:1px solid var(--line)}}
 #toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
 /* prompt-suggestion chips */
 .chips{display:flex;flex-wrap:wrap;gap:8px;margin:-14px 0 24px}
 .chip{font:inherit;font-size:13px;color:var(--mut);border:1px solid var(--line);background:var(--bg);
   border-radius:999px;padding:7px 13px;cursor:pointer;transition:.12s}
 .chip:hover{border-color:var(--sub);color:var(--ink);transform:translateY(-1px)}
 /* generation progress */
 .adprog{margin:16px 0 2px;display:flex;flex-direction:column;gap:10px}
 .adprog[hidden]{display:none}
 .adprog-track{height:7px;border-radius:999px;background:var(--field);overflow:hidden}
 .adprog-fill{height:100%;width:0;border-radius:999px;background:var(--accent);
   transition:width .6s ease;position:relative;overflow:hidden}
 .adprog-fill::after{content:"";position:absolute;inset:0;
   background:linear-gradient(90deg,transparent,rgba(255,255,255,.4),transparent);animation:shimmer 1.15s linear infinite}
 @keyframes shimmer{from{transform:translateX(-100%)}to{transform:translateX(100%)}}
 .adprog-msg{font-size:14.5px;font-weight:600;color:var(--ink);display:flex;align-items:center;gap:10px}
 .adprog-msg::before{content:"";width:9px;height:9px;border-radius:99px;background:var(--accent);
   flex:none;animation:pulse2 1s ease-in-out infinite}
 @keyframes pulse2{0%,100%{opacity:.3;transform:scale(.8)}50%{opacity:1;transform:scale(1)}}
 @media(prefers-reduced-motion:reduce){*{transition:none!important}
   .adprog-fill::after,.adprog-msg::before{animation:none}}
</style></head><body>
<header>
 <span class="brand">Trawl</span>
 <nav class="tabs">
   <button class="tab active" data-view="gen" onclick="showTab('gen')">Generate</button>
   <button class="tab" data-view="review" onclick="showTab('review')">Review</button>
 </nav>
 <span class="spacer"></span>
 <span class="count" id="count">—</span>
 <a class="ghost" href="/console">Console</a>
</header>
<main>
 <section id="view-gen" class="view">
   <div class="panel">
     <p class="kicker">New post</p>
     <h2>What should Trawl make?</h2>
     <p class="hint">Choose a type, add optional direction, and queue it. When you tell Claude to
       “process the post queue,” each post is built to that type's rules — honoring your direction.</p>
     <label class="fl">Post type</label>
     <div class="segs">
       <button class="seg active" data-type="linkedin" onclick="pickType('linkedin')">
         <span class="row"><span class="ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 6h16M4 12h16M4 18h11"/></svg></span><b>LinkedIn</b></span>
         <span>Educational · explains how it applies · no gate · signed</span></button>
       <button class="seg" data-type="twitter" onclick="pickType('twitter')">
         <span class="row"><span class="ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H8l-4 4V5a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2z"/></svg></span><b>X / Twitter</b></span>
         <span>Punchy · funnel-first · reply-keyword</span></button>
       <button class="seg" data-type="aifeature" onclick="pickType('aifeature')">
         <span class="row"><span class="ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.8 4.9L18.5 9l-4.7 1.1L12 15l-1.8-4.9L5.5 9l4.7-1.1zM18 14.5l.9 2.3 2.3.7-2.3.9L18 21l-.9-2.3-2.3-.9 2.3-.7z"/></svg></span><b>AI feature</b></span>
         <span>What's new in AI · educational · no gate</span></button>
       <button class="seg" data-type="ad" onclick="pickType('ad')">
         <span class="row"><span class="ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="10" r="1.6"/><path d="M21 16l-5-5-8 8"/></svg></span><b>Ad</b></span>
         <span>A finished ad creative · shows off AI-made design</span></button>
       <button class="seg" data-type="poc" onclick="pickType('poc')">
         <span class="row"><span class="ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3l14 8-6 1.6L10 19z"/></svg></span><b>Proof of concept</b></span>
         <span>A real working app you can click · saved to the vault</span></button>
     </div>
     <label class="fl" for="gen-note">Direction <span class="opt">— optional</span></label>
     <textarea id="gen-note" rows="4" placeholder="Describe the subject, angle, or tone you want — e.g. “the AI interview-coach idea, calm and credible, aimed at recent grads”. The post-type rules always still apply."></textarea>
     <div class="genrow">
       <div class="cnt"><label for="gen-count">How many</label><input type="number" id="gen-count" min="1" max="10" value="1"></div>
       <button class="primary" onclick="generate(event)">Generate &amp; queue</button>
       <span class="count" id="reqcount"></span>
     </div>
   </div>

   <div class="panel" style="margin-top:22px">
     <p class="kicker">Motion ad · video</p>
     <h2>Generate an ad video</h2>
     <p class="hint">Turn a one-line direction into a finished motion ad — it renders right here and
       lands in Review. Pick a speed mode: the visual layer is what takes the time.</p>
     <label class="fl" for="ad-note">Direction</label>
     <textarea id="ad-note" rows="2" placeholder="e.g. “Ship the ad, not the brief” — the headline/direction in one line."></textarea>
     <div class="chips" id="ad-sug"></div>
     <label class="fl">Speed mode</label>
     <div class="segs" id="ad-modes">
       <button class="seg active" data-mode="fast" onclick="pickMode('fast')">
         <span class="row"><b>Fast</b><span class="tag" style="margin-left:auto">~1 min</span></span>
         <span>Animated type only — no GPU</span></button>
       <button class="seg" data-mode="still" onclick="pickMode('still')">
         <span class="row"><b>Still</b><span class="tag" style="margin-left:auto">~2 min</span></span>
         <span>AI image behind motion text</span></button>
       <button class="seg" data-mode="motion" onclick="pickMode('motion')">
         <span class="row"><b>Motion</b><span class="tag" style="margin-left:auto">~5 min</span></span>
         <span>AI video b-roll (Wan)</span></button>
     </div>
     <div class="genrow">
       <div class="cnt"><label for="ad-aspect">Aspect</label>
         <select id="ad-aspect" style="width:auto"><option>16:9</option><option>1:1</option><option>4:5</option><option>9:16</option></select></div>
       <button class="primary" id="ad-go" onclick="makeAd(event)">Generate ad</button>
     </div>
     <div class="adprog" id="ad-prog" hidden>
       <div class="adprog-track"><div class="adprog-fill" id="ad-fill"></div></div>
       <div class="adprog-msg"><span id="ad-msg"></span>
         <button class="chip" id="ad-cancel" onclick="cancelAd()" style="margin-left:auto">Cancel</button></div>
     </div>
   </div>
 </section>
 <section id="view-review" class="view" hidden>
   <div class="revbar">
     <select id="sort" onchange="render()" title="Sort order">
       <option value="new">Newest first</option><option value="old">Oldest first</option>
     </select>
     <button class="ghost" onclick="load()">↻ Refresh</button>
     <button class="ghost" onclick="openFolder(null)" title="Open the central PNG exports folder">📁 Screenshots</button>
   </div>
   <div id="list"></div>
 </section>
</main>
<div id="toast"></div>
<script>
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let posts=[],approvals={},used={},selType='linkedin';
function toast(m){const t=document.getElementById('toast');t.textContent=m;t.classList.add('show');
  clearTimeout(t._t);t._t=setTimeout(()=>t.classList.remove('show'),2400);}
async function copy(text){try{await navigator.clipboard.writeText(text);return true;}catch(e){
  const a=document.createElement('textarea');a.value=text;a.style.cssText='position:fixed;left:-9999px';
  document.body.appendChild(a);a.select();const ok=document.execCommand('copy');a.remove();return ok;}}
function showTab(v){
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.dataset.view===v));
  document.getElementById('view-gen').hidden=v!=='gen';
  document.getElementById('view-review').hidden=v!=='review';
  if(v==='review')load();
}
function pickType(t){selType=t;
  document.querySelectorAll('.segs:not(#ad-modes) .seg').forEach(s=>s.classList.toggle('active',s.dataset.type===t));}
let selMode='fast';
function pickMode(m){selMode=m;
  document.querySelectorAll('#ad-modes .seg').forEach(s=>s.classList.toggle('active',s.dataset.mode===m));
  const g=document.getElementById('ad-go');if(g&&!g.disabled)g.textContent=goLabel();}
// Suggested directions — curated from the ad DNA (Creatify hooks + funnel promo voice). Edit freely.
const AD_SUGGESTIONS=['Ship the ad, not the brief','Your growth, on autopilot',
  'We built this in an afternoon','The $47 tool that replaced my agency',
  'Stop paying for what AI builds free','One prompt in, a finished ad out'];
function renderSug(){const c=document.getElementById('ad-sug');if(c)
  c.innerHTML=AD_SUGGESTIONS.map(s=>`<button class="chip" onclick="useSug(this)">${esc(s)}</button>`).join('');}
function useSug(el){const t=document.getElementById('ad-note');t.value=el.textContent;t.focus();}
const MODE_EST={fast:70,still:130,motion:320};
const fmtT=s=>{s=Math.max(0,s|0);return (s/60|0)+':'+String(s%60).padStart(2,'0')};
const MODE_LABEL={fast:'~1 min',still:'~2 min',motion:'~5 min'};
function goLabel(){return 'Generate ad · '+(MODE_LABEL[selMode]||'');}
let adPoll=null,adStart=0,adEst=70,adJobId=null;
function adReset(){document.getElementById('ad-prog').hidden=true;
  const b=document.getElementById('ad-go');b.disabled=false;b.textContent=goLabel();
  const c=document.getElementById('ad-cancel');if(c){c.disabled=false;c.textContent='Cancel';}}
async function cancelAd(){
  if(!adJobId)return;
  const c=document.getElementById('ad-cancel');c.disabled=true;c.textContent='Cancelling…';
  try{await fetch('/api/cancel_ad',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:adJobId})});}catch(e){}
}
async function makeAd(ev){
  const note=document.getElementById('ad-note').value.trim();
  const aspect=document.getElementById('ad-aspect').value;
  const btn=document.getElementById('ad-go');
  const prog=document.getElementById('ad-prog'),fill=document.getElementById('ad-fill'),msg=document.getElementById('ad-msg');
  adJobId=null;btn.disabled=true;btn.textContent='Rendering…';prog.hidden=false;fill.style.width='4%';msg.textContent='Starting…';
  document.getElementById('ad-cancel').disabled=false;document.getElementById('ad-cancel').textContent='Cancel';
  try{
    const r=await(await fetch('/api/make_ad',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({note,mode:selMode,aspect})})).json();
    if(!r.ok){adReset();toast(r.error||'Could not start');return;}
    adJobId=r.id;adStart=Date.now();adEst=MODE_EST[selMode]||120;pollAd(r.id);
  }catch(e){adReset();toast('Could not start render');}
}
function pollAd(id){
  const fill=document.getElementById('ad-fill'),msg=document.getElementById('ad-msg');
  if(adPoll)clearInterval(adPoll);
  adPoll=setInterval(async()=>{
    let job;try{job=(await(await fetch('/api/ad_status')).json()).jobs[id];}catch(e){return;}
    if(!job)return;
    const el=(Date.now()-adStart)/1000;
    msg.textContent=fmtT(el)+' · '+(job.msg||job.state);   // live elapsed clock + stage
    const pct=Math.min(94,el/adEst*100);   // time-estimated; never 100 until done
    fill.style.width=Math.max(4,pct).toFixed(0)+'%';
    if(job.state==='done'){clearInterval(adPoll);fill.style.width='100%';msg.textContent='Done';
      setTimeout(()=>{adReset();toast('Ad ready — opening Review');showTab('review');},700);}
    else if(job.state==='cancelled'){clearInterval(adPoll);adReset();toast('Render cancelled');}
    else if(job.state==='error'){clearInterval(adPoll);adReset();toast('Render failed: '+job.msg);}
  },1200);
}
async function generate(ev){
  const note=document.getElementById('gen-note').value.trim();
  const n=Math.max(1,Math.min(+document.getElementById('gen-count').value||1,10));
  const btn=ev&&ev.currentTarget;if(btn){btn.disabled=true;btn.textContent='Generating…';}
  try{
    const r=await(await fetch('/api/queue_generate',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({type:selType,note,n})})).json();
    if(r.ok){toast(`Queued ${r.queued} ${selType} post${r.queued===1?'':'s'} — tell Claude “process the post queue”`);
      loadRequests();}
    else toast(r.error||'Could not queue');
  }finally{if(btn){btn.disabled=false;btn.textContent='Generate & queue';}}
}
async function load(){
  const d=await(await fetch('/api/posts')).json();
  posts=d.posts||[];approvals=d.approvals||{};
  render();loadRequests();
}
async function loadRequests(){
  const d=await(await fetch('/api/requests')).json();
  const n=(d.requests||[]).filter(r=>r.status==='pending').length;
  document.getElementById('reqcount').textContent=n?`${n} queued — tell Claude “process the post queue”`:'';
}
function render(){
  const dir=document.getElementById('sort').value;
  const arr=[...posts].sort((a,b)=>dir==='old'?a.mtime-b.mtime:b.mtime-a.mtime);
  const reviewed=Object.values(approvals).filter(a=>a&&a.approved!==null).length;
  const posted=posts.filter(p=>p.status==='posted').length;
  document.getElementById('count').textContent=`${posts.length} posts · ${reviewed} reviewed · ${posted} posted`;
  document.getElementById('list').innerHTML=arr.length?arr.map(p=>{
    const a=approvals[p.slug]||{};
    const cls=[p.status==='posted'?'posted':'',a.approved===true?'approved':a.approved===false?'rejected':''].join(' ');
    const styleLabel=p.style==='twitter'?'X / Twitter':p.style==='ad'?'Ad':p.style==='poc'?'PoC':'LinkedIn';
    return `<article class="post ${cls}">
      ${p.status==='posted'?`<span class="posted-badge">Posted</span>`:''}
      ${p.has_video?`<video class="frame" src="/vid/${p.slug}" controls muted loop playsinline preload="metadata"></video>`
        :p.has_poc?`<iframe class="frame" src="/poc/${p.slug}" title="${esc(p.artifact)}" loading="lazy"></iframe>`:''}
      <div class="meta">
        <div class="cat">${esc(p.category||'—')}<span class="tag">${styleLabel}</span>
          ${p.status==='posted'?`<span class="tag">posted ${esc(p.platform)} ${esc(p.date)}</span>`:''}</div>
        <p class="hook">${esc(p.hook)}</p>
        <p class="sub">${esc(p.artifact)}${p.keyword?` · reply “${esc(p.keyword)}”`:''}${p.format?' · '+esc(p.format):''}</p>
        ${p.body?`<div class="body"><div class="cap">${styleLabel} caption
          <button class="copybtn" onclick="copyBody('${p.slug}')">Copy</button></div>${esc(p.body)}</div>`:''}
      </div>
      <div class="bar">
        <button class="primary" onclick="postTo('${p.slug}','linkedin')">Post to LinkedIn</button>
        <button class="primary" onclick="postTo('${p.slug}','x')">Post to X</button>
        <button onclick="window.open('${p.has_video?'/vid/':'/poc/'}${p.slug}','_blank')">⤢ ${p.has_video?'Video':'Poster'}</button>
        <button onclick="savePng('${p.slug}',this)" ${p.has_poc?'':'disabled title="Needs poc.html"'}
          title="Save poster as PNG (also lands in Posts/_exports)">⬇ PNG</button>
        ${p.has_poster?`<img class="png-thumb" src="/png/${p.slug}?t=${Math.round(p.mtime)}" draggable="true"
          ondragstart="dragPng(event,'${p.slug}')" title="Drag onto Desktop or a folder" alt="">`:''}
        <button onclick="openFolder('${p.slug}')" title="Open this post's folder in Explorer">📁 Folder</button>
        <span class="sep"></span>
        <button class="approve ${a.approved===true?'on':''}" onclick="mark('${p.slug}',true)">✓ Approve</button>
        <button class="reject ${a.approved===false?'on':''}" onclick="mark('${p.slug}',false)">✕</button>
        <span class="sep"></span>
        <button onclick="markPosted('${p.slug}')">Mark posted</button>
        <span class="state" style="margin-left:auto">${a.approved===true?'approved':a.approved===false?'rejected':'unreviewed'}</span>
        <div class="note"><input placeholder="note — the signal that tunes Trawl (optional)"
          value="${esc(a.note||'')}" onchange="note('${p.slug}',this.value)"></div>
      </div></article>`;
  }).join(''):'<div class="empty">No posts yet. Generate some, then process the queue.</div>';
}
function copyBody(slug){
  const p=posts.find(x=>x.slug===slug);if(!p)return;
  copy(p.body||'').then(ok=>toast(ok?'caption copied':'select + copy manually'));
}
function postTo(slug,platform){
  const p=posts.find(x=>x.slug===slug);if(!p)return;
  used[slug]=used[slug]||new Set();used[slug].add(platform);
  window.open((p.has_video?'/vid/':'/poc/')+slug,'_blank');
  if(platform==='x'){
    window.open('https://twitter.com/intent/tweet?text='+encodeURIComponent(p.x_body||p.hook),'_blank');
    toast('X composer opened — use ⬇ PNG then attach (or drag the thumb)');
  }else{
    copy(p.li_body||p.hook).then(()=>{
      window.open('https://www.linkedin.com/feed/?shareActive=true','_blank');
      toast('LinkedIn text copied — use ⬇ PNG then attach (or drag the thumb)');});
  }
}
async function savePng(slug,btn){
  const p=posts.find(x=>x.slug===slug);if(!p||!p.has_poc){toast('No HTML poster to capture');return;}
  if(btn){btn.disabled=true;btn.textContent='…';}
  try{
    const r=await(await fetch('/api/screenshot',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({slug})})).json();
    if(!r.ok){toast(r.error||'Capture failed');return;}
    // browser download + keep a local copy under the post + _exports
    const a=document.createElement('a');
    a.href='/png/'+slug+'?download=1&t='+Date.now();
    a.download=(slug||'poster')+'.png';
    document.body.appendChild(a);a.click();a.remove();
    p.has_poster=true;render();
    toast(r.cached?'PNG ready (cached) — drag the thumb or check Downloads':
      'PNG saved — drag the thumb, or open 📁 Screenshots');
  }catch(e){toast('Capture failed');}
  finally{if(btn){btn.disabled=false;btn.textContent='⬇ PNG';}}
}
function dragPng(ev,slug){
  // Chrome/Edge: dragging this thumb onto Desktop/Explorer drops the real PNG file.
  const url=location.origin+'/png/'+slug;
  const name=(slug||'poster')+'.png';
  try{ev.dataTransfer.setData('DownloadURL','image/png:'+name+':'+url);}catch(e){}
  ev.dataTransfer.setData('text/uri-list',url);
  ev.dataTransfer.effectAllowed='copy';
}
async function openFolder(slug){
  try{
    const body=slug?{slug}:{exports:true};
    const r=await(await fetch('/api/open_folder',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)})).json();
    if(!r.ok)toast(r.error||'Could not open folder');
  }catch(e){toast('Could not open folder');}
}
async function markPosted(slug){
  const set=used[slug];
  const platform=!set?'manual':set.size===2?'both':[...set][0]==='x'?'X':'LinkedIn';
  await fetch('/api/post',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({slug,platform})});
  toast('Marked posted ('+platform+') in POSTING-LOG');load();
}
async function mark(slug,approved){
  const cur=approvals[slug]||{};
  if(cur.approved===approved)approved=null;
  await fetch('/api/mark',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({slug,approved,note:cur.note||''})});
  load();
}
async function note(slug,note){
  const cur=approvals[slug]||{};
  await fetch('/api/mark',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({slug,approved:cur.approved??null,note})});
}
// heartbeat — the desktop watchdog self-exits after ~45s without a ping, so closing the window
// stops the backend and leaves no orphan process.
async function ping(){try{await fetch('/api/ping');}catch(e){}}
ping();setInterval(ping,10000);
addEventListener('keydown',e=>{if(e.key==='Escape')window.close();});
renderSug();pickMode('fast');load();
</script></body></html>""".replace("__FAVICON__", FAVICON)
