#!/usr/bin/env python3
"""Trawl — press a button, get an idea and a prompt.

Standard library only. No pip installs.

    python trawl.py app       serve for the desktop window (auto-refresh, auto-shutdown)
    python trawl.py fetch     pull + score + store
    python trawl.py serve     serve only, no watchdog
    python trawl.py top -n 10 print to terminal
"""

import argparse
import json
import os
import random
import re
import sys
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import board
import idea
import library
import prompt as promptgen
import rank
import sources
import store

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
UI_PATH = ROOT / "ui" / "index.html"

# Where the built posts + POSTING-LOG + approvals + requests live (the Ship Something Real
# Posts folder). The review board reads/writes here; nothing post-related lives in this repo.
DEFAULT_POSTS_DIR = (r"C:\Users\losth\Documents\ClaudeBrain"
                     r"\02 Projects\AI-Beginner-Business\Posts")

STALE_AFTER_H = 6          # auto-refresh if data older than this
# No heartbeat for this long -> quit, so closing the window never leaves an orphan process.
# Overridable for testing: TRAWL_IDLE=8 python trawl.py app
IDLE_SHUTDOWN_S = int(os.environ.get("TRAWL_IDLE", "45"))

_state = {"last_ping": 0.0, "armed": False, "fetching": False, "srv": None}

# In-memory ad-render jobs (make_ad runs minutes for Wan, so it can't block an HTTP request).
_ad_jobs = {}


def _run_ad_job(job_id, note, mode, aspect):
    """Background worker: compose a brief, render the ad, drop it into the review board."""
    import ad
    import library
    job = _ad_jobs[job_id]
    try:
        job.update(state="running", msg="Composing brief…")
        brief = ad.compose_brief(note, aspect=aspect, **ad.mode_overrides(mode))
        job["title"] = brief["headline"]
        outdir = str(Path(__file__).resolve().parent / "out")
        mp4 = ad.make_ad(brief, outdir=outdir, on_status=lambda m: job.update(msg=m),
                         should_cancel=lambda: _ad_jobs.get(job_id, {}).get("cancel"))
        job.update(msg="Saving to the review board…")
        folder = ad.save_to_posts(brief, mp4, board.POSTS_DIR)
        try:
            library.refresh(board.POSTS_DIR)
        except Exception:
            pass
        job.update(state="done", msg="Done — see the Review tab", slug=Path(folder).name)
    except Exception as e:
        import render
        cancelled = isinstance(e, render.Cancelled)
        job.update(state="cancelled" if cancelled else "error",
                   msg="Cancelled" if cancelled else str(e)[:400])


def do_setup():
    """Interactive Reddit credential entry. The secret is never echoed or logged."""
    import getpass

    print("\n  Reddit API setup")
    print("  " + "-" * 58)
    print("""
  Trawl needs read-only Reddit credentials. Reddit blocks unauthenticated
  requests (verified: 403), so this is required for any Reddit data.

  1. Open  https://www.reddit.com/prefs/apps
  2. Click "create another app..." at the bottom
  3. Name:         trawl
     Type:         script          <-- must be "script"
     redirect uri: http://localhost:8080
  4. Click "create app"
  5. The CLIENT ID is the short string directly under the app name.
     The SECRET is the longer string labelled "secret".

  Trawl uses app-only OAuth. It never asks for your Reddit password and
  cannot post, vote, or read anything private.
""")
    cfg = load_config()
    rd = cfg.setdefault("reddit", {})

    cid = input("  Client ID     : ").strip()
    if not cid:
        print("\n  Nothing entered — aborted, config unchanged.")
        return 1
    secret = getpass.getpass("  Client secret : ").strip()   # not echoed
    if not secret:
        print("\n  No secret entered — aborted, config unchanged.")
        return 1
    user = input("  Reddit username (for the user-agent, optional): ").strip() or "unknown"

    rd["client_id"] = cid
    rd["client_secret"] = secret
    rd["user_agent"] = f"windows:trawl:0.1 (by /u/{user})"
    rd["enabled"] = True

    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(f"\n  Saved to {CONFIG_PATH}")
    print("  This file is gitignored. Do not paste its contents anywhere.\n")

    print("  Testing...")
    try:
        token = sources.reddit_token(rd)
        print(f"  OK — got an access token ({len(token)} chars).")
    except Exception as e:
        print(f"  FAILED: {e}")
        print("  Check the ID and secret, and that the app type is 'script'.")
        return 1

    print("\n  Now run:  python trawl.py fetch\n")
    return 0


def _merge_defaults(base, defaults):
    """Fill in keys the live config is missing, without touching what it already sets."""
    for k, v in defaults.items():
        if k not in base:
            base[k] = v
        elif isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge_defaults(base[k], v)
    return base


def load_config():
    """Live config over example defaults.

    config.json is written once and then edited by hand (or by `setup`), so new keys added to
    config.example.json used to be invisible until someone re-copied the file. That silently
    cost us the whole `quant` source and the repointed queries — the fetch ran happily against
    a stale config. Merging means new options appear automatically; your keys stay yours.
    """
    defaults = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    if not CONFIG_PATH.exists():
        return defaults
    live = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return _merge_defaults(live, defaults)


# ------------------------------------------------------------------ pipeline


def do_fetch(cfg, quiet=False):
    def say(*a):
        if not quiet:
            print(*a)

    conn = store.connect()
    items = []

    say("Fetching...")
    hn_cfg = cfg.get("hackernews", {})
    if hn_cfg.get("enabled"):
        try:
            items += sources.fetch_hn(hn_cfg)
        except Exception as e:
            say(f"  ! hackernews failed: {e}")

    se_cfg = cfg.get("stackexchange", {})
    if se_cfg.get("enabled"):
        try:
            items += sources.fetch_stackexchange(se_cfg)
        except Exception as e:
            say(f"  ! stackexchange failed: {e}")

    rd_cfg = cfg.get("reddit", {})
    if rd_cfg.get("enabled"):
        try:
            items += sources.fetch_reddit(rd_cfg)
        except Exception as e:
            say(f"  ! reddit skipped: {e}")

    # AI-feature feelers (kind="ai-feature"). These bypass rank.score_all — its gates are tuned
    # for business-pain posts and would wrongly drop a model listing or a paper title. They're
    # curated feeds already, so light dedup + straight into the pool.
    ai_items = []
    for key, fn in (("huggingface", sources.fetch_huggingface),
                    ("github", sources.fetch_github),
                    ("arxiv", sources.fetch_arxiv)):
        c = cfg.get(key, {})
        if c.get("enabled"):
            try:
                ai_items += fn(c)
            except Exception as e:
                say(f"  ! {key} failed: {e}")

    # Curated content ideas from the vault Ideas-Bank (hand-written seeds, not scraped).
    idea_cfg = cfg.get("ideas", {})
    idea_items = sources.fetch_ideas(idea_cfg) if idea_cfg.get("enabled") else []

    if not items and not ai_items and not idea_items:
        conn.close()
        return 0

    # Dedupe business on id AND normalised title (HN reposts stories under new objectIDs).
    def title_key(t):
        return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()

    seen_id, seen_title, unique = set(), set(), []
    for it in items:
        tk = title_key(it.get("title"))
        if it["id"] in seen_id or (tk and tk in seen_title):
            continue
        seen_id.add(it["id"])
        seen_title.add(tk)
        unique.append(it)

    known = {r["id"] for r in conn.execute("SELECT id FROM items").fetchall()}
    # score_all is now CLEANING ONLY (drops junk/offtopic/inert). The rank_score it produces is
    # no longer used for ordering — generate() draws from the pool at random. The weight system
    # is retired; the filtering it did is worth keeping so garbage stays out of the pool.
    ranked = rank.score_all(unique, known_ids=known)
    for it in ranked:
        store.upsert(conn, it)             # kind defaults to "business" in store.upsert

    seen_ai = set()
    for it in ai_items:
        if it["id"] not in seen_ai:
            seen_ai.add(it["id"])
            store.upsert(conn, it)         # carries kind="ai-feature"

    for it in idea_items:
        store.upsert(conn, it)             # curated ideas: kind="business", bypass the junk gate

    conn.commit()
    say(f"\n{len(ranked)} business + {len(seen_ai)} ai-feature + {len(idea_items)} ideas. "
        f"Pool holds {store.stats(conn)['total']}.")
    conn.close()
    return len(ranked) + len(seen_ai)


def data_age_hours():
    conn = store.connect()
    row = conn.execute("SELECT MAX(last_seen) m FROM items").fetchone()
    conn.close()
    if not row or not row["m"]:
        return 1e9
    return (time.time() - row["m"]) / 3600.0


def background_fetch():
    if _state["fetching"]:
        return
    _state["fetching"] = True

    def work():
        try:
            do_fetch(load_config(), quiet=True)
        except Exception:
            pass
        finally:
            _state["fetching"] = False

    threading.Thread(target=work, daemon=True).start()


def do_top(n):
    conn = store.connect()
    rows = store.top(conn, n)
    if not rows:
        print("Nothing stored yet. Run: python trawl.py fetch")
        return
    print(f"\n{'#':>3}  {'score':>6}  {'theme':<18} title")
    print("-" * 100)
    for i, r in enumerate(rows, 1):
        print(f"{i:>3}  {r['rank_score']:>6.3f}  {r['theme']:<18} {r['title'][:60]}")
    print()
    conn.close()


# ------------------------------------------------------------------ server


def generate_specs(ptype, n, note=""):
    """Draw random pool material into N post specs of a type, each with a build prompt.

    The single source of truth for generation. `note` is the operator's free-text direction from
    the Generate form — it's injected into every build prompt (steering subject/angle/tone without
    breaking the post-type rules) and stored on each spec so the queued request keeps it."""
    n = max(1, min(int(n), 20))
    kind = idea.POST_TYPES.get(ptype, idea.POST_TYPES["linkedin"])["kind"]
    conn = store.connect()
    pool = store.pool(conn, kind=kind, n=max(n * 5, 25))
    conn.close()
    specs = idea.generate(pool, ptype, n)
    for s in specs:
        s["note"] = note
        s["prompt"] = (promptgen.build_ai_feature(s, note=note) if s.get("kind") == "ai-feature"
                       else promptgen.build_idea(s, mode=s.get("mode", "gated"),
                                                 style=s.get("style"), note=note))
    return specs, len(pool)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)

        if u.path in ("/", "/index.html"):
            # The review board is the front door of the combined app.
            return self._send(200, board.PAGE, "text/html; charset=utf-8")

        if u.path == "/console":
            # The old generator console — raw specs, copy hook/prompt. Secondary now.
            if not UI_PATH.exists():
                return self._send(500, "ui/index.html missing", "text/plain")
            return self._send(200, UI_PATH.read_bytes(), "text/html; charset=utf-8")

        if u.path == "/api/posts":
            return self._send(200, json.dumps({
                "posts": board.read_posts(), "approvals": board.load_approvals(),
            }))

        if u.path == "/api/requests":
            return self._send(200, json.dumps({"requests": board.read_requests()}))

        if u.path == "/api/ad_status":
            return self._send(200, json.dumps({"jobs": _ad_jobs}))

        if u.path == "/api/library":
            # The notes index over every past post (coverage, voice, reusable POC patterns).
            return self._send(200, json.dumps(library.build_library(board.POSTS_DIR)))

        m = re.match(r"/poc/([\w-]+)$", u.path)
        if m:
            data = board.poc_bytes(m.group(1))
            if data is not None:
                return self._send(200, data, "text/html; charset=utf-8")
            return self._send(404, "not found", "text/plain")

        m = re.match(r"/vid/([\w-]+)$", u.path)
        if m:
            data = board.video_bytes(m.group(1))
            if data is not None:
                return self._send(200, data, "video/mp4")
            return self._send(404, "not found", "text/plain")

        if u.path == "/api/ping":
            _state["last_ping"] = time.time()
            _state["armed"] = True
            return self._send(200, json.dumps({
                "ok": True,
                "fetching": _state["fetching"],
                "age_h": round(data_age_hours(), 1),
            }))

        if u.path == "/api/stats":
            # Pool health for the generator UI: how much material, by kind, and how fresh.
            conn = store.connect()
            rows = conn.execute("SELECT kind, COUNT(*) n FROM items GROUP BY kind").fetchall()
            conn.close()
            counts = {r["kind"] or "business": r["n"] for r in rows}
            return self._send(200, json.dumps({
                "business": counts.get("business", 0),
                "ai_feature": counts.get("ai-feature", 0),
                "total": sum(counts.values()),
                "age_h": round(data_age_hours(), 1),
                "fetching": _state["fetching"],
            }))

        if u.path == "/api/generate":
            # Aggregate random pool material into N posts of a TYPE.
            ptype = q.get("type", ["linkedin"])[0]
            specs, pool_size = generate_specs(ptype, int(q.get("n", ["1"])[0]),
                                              note=q.get("note", [""])[0])
            return self._send(200, json.dumps({
                "specs": specs, "type": ptype, "pool_size": pool_size,
                "fetching": _state["fetching"],
            }))

        if u.path == "/api/refresh":
            background_fetch()
            return self._send(200, json.dumps({"started": True}))

        return self._send(404, json.dumps({"error": "no route"}))

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._send(400, json.dumps({"error": "bad json"}))

        if u.path == "/api/quit":
            threading.Thread(target=_shutdown, daemon=True).start()
            return self._send(200, json.dumps({"ok": True}))

        if u.path == "/api/mark":
            a = board.load_approvals()
            a[payload.get("slug")] = {"approved": payload.get("approved"),
                                      "note": payload.get("note", "")}
            board.save_approvals(a)
            return self._send(200, json.dumps({"ok": True}))

        if u.path == "/api/post":
            ok = board.mark_posted(payload.get("slug", ""), payload.get("platform", "manual"))
            return self._send(200, json.dumps({"ok": ok}))

        if u.path == "/api/make_ad":
            # Render a motion ad in the background (mode = fast/still/motion). Poll /api/ad_status.
            import ad
            note = (payload.get("note") or "").strip()
            mode = payload.get("mode", ad.DEFAULT_MODE)
            aspect = payload.get("aspect", "16:9")
            if mode not in ad.MODES:
                return self._send(400, json.dumps({"error": f"unknown mode {mode!r}"}))
            if aspect not in ad.ASPECTS:
                return self._send(400, json.dumps({"error": f"unknown aspect {aspect!r}"}))
            job_id = f"ad-{int(time.time() * 1000)}"
            _ad_jobs[job_id] = {"id": job_id, "state": "queued", "msg": "Queued…",
                                "mode": mode, "aspect": aspect, "created": time.time()}
            # keep the map small — drop all but the 20 most recent
            for old in sorted(_ad_jobs, key=lambda k: _ad_jobs[k]["created"])[:-20]:
                _ad_jobs.pop(old, None)
            threading.Thread(target=_run_ad_job, args=(job_id, note, mode, aspect),
                             daemon=True).start()
            return self._send(200, json.dumps({"ok": True, "id": job_id}))

        if u.path == "/api/cancel_ad":
            job = _ad_jobs.get(payload.get("id"))
            if not job:
                return self._send(404, json.dumps({"error": "no such job"}))
            job["cancel"] = True
            import render
            render.interrupt()          # stop the in-flight GPU render immediately
            return self._send(200, json.dumps({"ok": True}))

        if u.path == "/api/queue_generate":
            # Single-generation flow: one post TYPE + a free-text direction NOTE + a count.
            # The note is injected into each build prompt and stored on the request.
            ptype = payload.get("type", "linkedin")
            if ptype not in idea.POST_TYPES:
                ptype = "linkedin"
            n = max(1, min(int(payload.get("n", 1) or 1), 20))
            note = (payload.get("note") or "").strip()
            specs, _ = generate_specs(ptype, n, note=note)
            queued = board.queue_specs(ptype, specs)
            # Refresh the notes library now, so it's current when Claude builds the just-queued
            # posts (their prompts tell it to read Posts\LIBRARY.md).
            try:
                library.refresh(board.POSTS_DIR)
            except Exception:
                pass
            return self._send(200, json.dumps({"ok": True, "queued": queued}))

        return self._send(404, json.dumps({"error": "no route"}))


def _shutdown():
    time.sleep(0.3)
    srv = _state.get("srv")
    if srv:
        srv.shutdown()


def _watchdog():
    """Quit when the window goes away, so no python process is left orphaned."""
    while True:
        time.sleep(5)
        if not _state["armed"]:
            continue
        if time.time() - _state["last_ping"] > IDLE_SHUTDOWN_S:
            _shutdown()
            return


def serve(cfg, watchdog=False, open_browser=False):
    port = int(cfg.get("server", {}).get("port", 8420))
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    _state["srv"] = srv

    if data_age_hours() > STALE_AFTER_H:
        background_fetch()

    if watchdog:
        threading.Thread(target=_watchdog, daemon=True).start()
    else:
        print(f"\nTrawl at http://localhost:{port}   (Ctrl+C to stop)")
        if open_browser:
            webbrowser.open(f"http://localhost:{port}")

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


# ------------------------------------------------------------------ cli


def main():
    ap = argparse.ArgumentParser(description="Trawl")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("app")
    sub.add_parser("fetch")
    sub.add_parser("serve")
    sub.add_parser("setup")
    sub.add_parser("library")
    a = sub.add_parser("ad")           # idea/brief -> animated mp4 ad (Rungs 1-4)
    a.add_argument("brief", nargs="?", help="path to a brief.json (omit for the demo brief)")
    a.add_argument("--note", default=None, help="compose a brief from a free-text direction note")
    a.add_argument("--to-posts", action="store_true", help="drop the finished ad into the review board")
    a.add_argument("-o", "--outdir", default="out")
    t = sub.add_parser("top")
    t.add_argument("-n", type=int, default=15)

    args = ap.parse_args()
    cmd = args.cmd or "app"

    if cmd == "top":
        return do_top(args.n)
    if cmd == "setup":
        return do_setup()
    if cmd == "ad":
        import ad
        if args.note is not None:
            brief = ad.compose_brief(args.note)          # brief EMITTER: note -> full brief
        elif args.brief:
            brief = ad.new_brief(**json.loads(Path(args.brief).read_text("utf-8")))
        else:
            brief = ad.demo_brief()
        mp4 = ad.make_ad(brief, args.outdir)
        print("->", mp4)
        if args.to_posts:
            posts_dir = load_config().get("posts_dir", DEFAULT_POSTS_DIR)
            print("   review board ->", ad.save_to_posts(brief, mp4, posts_dir))
        return 0

    cfg = load_config()
    posts_dir = cfg.get("posts_dir", DEFAULT_POSTS_DIR)
    board.configure(posts_dir)
    if cmd == "library":
        lib = library.refresh(posts_dir)
        print(f"Library rebuilt: {lib['count']} posts -> {posts_dir}\\LIBRARY.md (+ library.json)")
        return 0
    if cmd == "fetch":
        do_fetch(cfg)
    elif cmd == "app":
        serve(cfg, watchdog=True)
    else:
        serve(cfg, watchdog=False, open_browser=True)


if __name__ == "__main__":
    sys.exit(main())
