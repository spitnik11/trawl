# Trawl — Design & Architecture Doc

> A full technical design doc for research/handoff. Covers what Trawl is, how it's built, the data
> and pipeline, every module, how it runs, how it's tested, and how it's maintained on GitHub.
> (Note: `docs/DESIGN.md` in this repo is a separate *UI design-token* reference, not about Trawl.)
>
> Repo: **github.com/spitnik11/trawl** (private) · Local: `Z:\Claude app\trawl` · Last updated 2026-08-06.

---

## 1. What Trawl is

Trawl is a **local, single-process desktop app** that turns scraped internet material into
**ready-to-build social-content briefs** for a one-person AI-consulting content business
(the "Ship Something Real" funnel: LinkedIn/X posts → free guide → email list). It:

1. **Scrapes** developer/business communities and AI-release feeds.
2. **Cleans + pools** the material in SQLite.
3. **Generates** posts of a chosen type (LinkedIn, X, AI-feature, ad, PoC, carousel) by drawing
   randomly from the pool and rendering an opinionated **build prompt** you paste into Claude.
4. Claude builds the deliverable (a single-file HTML "screenshot", a working PoC, an ad video, or a
   carousel PDF); Trawl's **review board** shows it, and you approve + post.

**One idea → one native deliverable.** Trawl is the front half (research + brief + review); an
LLM (Claude) is the build half. The two are deliberately separate.

### Product philosophy (the load-bearing constraints)
- **Standard library only. Zero pip installs.** `urllib`, `sqlite3`, `http.server`, `json`, `re`,
  `subprocess`. No venv, no npm, no framework. Rationale: nothing to break, still runs in two years.
- **Every generated deliverable is a SINGLE HTML FILE** — no build step, no CDN, no webfonts, strict
  CSP, system fonts only. This is what makes output instantly postable; it also rules out React/
  Tailwind/Supabase stacks for the *output* (not the app).
- **Endless by construction.** Ranking/weighting is retired; generation draws at random from the
  pool, so it never runs out of posts.
- **Deterministic generation.** Hook/template choice uses `hashlib.sha1`, never `random`/`hash()`,
  so the same input always yields the same brief (a feed you can plan around).

---

## 2. Tech stack & runtime

| Layer | Choice |
|---|---|
| Language | **Python 3.10** (stdlib only) |
| Web server | `http.server.ThreadingHTTPServer` on `127.0.0.1:8420` |
| UI | One static HTML dashboard (string in `board.py`), vanilla JS, no framework |
| Storage | **SQLite** (`trawl.db`) for the item pool + dedupe/seen/posted state |
| HTTP/scraping | `urllib` (+ `gzip` for Stack Exchange), app-only OAuth for Reddit |
| Rendering (output) | Headless **Edge/Chrome** driven by CLI flags (`--screenshot`, `--print-to-pdf`) |
| GPU media (ads) | Talks over HTTP (urllib) to **ComfyUI** (:8188) and **PromptLens** (:8000); `ffmpeg` on PATH |
| Desktop shell | `Trawl.vbs` → `launch.ps1` → **`pythonw.exe`** (windowless) → Edge `--app`/Chrome tab |

External runtime dependencies are only: a Chromium-family browser (ships with Windows) and, for the
ad engine, ffmpeg + the local ComfyUI/PromptLens services. The core content pipeline needs neither.

---

## 3. Repository layout

```
trawl/
  trawl.py                 # thin shim: puts core/ on sys.path, calls core.cli.main()
  core/                    # the app (stdlib only, ~4,850 LOC)
    cli.py       (669)     # entry + HTTP server + routes + CLI subcommands + watchdog + ad jobs
    board.py     (773)     # review-board UI (the dashboard HTML/CSS/JS) + posts/approvals/log I/O
    prompt.py    (531)     # build-prompt templates (5 post types) + the design bar + honesty guards
    idea.py      (515)     # generate(): classify → hook → mode → spec (deterministic, no LLM)
    sources.py   (475)     # scrapers: HN, Stack Exchange, Reddit(OAuth), HF/GitHub/arXiv, Ideas-Bank
    ad.py        (449)     # ad engine: Creatify matrix → animated HTML → frames → mp4
    render.py    (389)     # ad visual layer: ComfyUI Z-Image still + Wan motion + Florence-2 verify
    rank.py      (351)     # cleaning-only gates (ranking retired; drops junk/off-topic)
    carousel.py  (259)     # spec → LinkedIn "document post" PDF via browser print-to-pdf
    library.py   (154)     # post-notes index → Posts/LIBRARY.md + library.json
    store.py     (145)     # SQLite: connect/migrate, upsert, pool(random draw), stats, top
    shot.py      (142)     # HTML/URL → PNG via headless Edge/Chrome (virtual-time-budget)
  ui/index.html            # legacy raw generator console (now /console)
  docs/                    # ARCHITECTURE.md (this), DESIGN.md (unrelated UI tokens), output-design-rules.md
  tests/                   # test_gates.py, test_idea.py (stdlib asserts, no pytest)
  scripts/make-icon.py     # stdlib multi-size .ico builder
  prototypes/              # Wan workflow builders + wan-brief.json (ad R&D, isolated)
  assets/  out/            # icon assets; generated output (gitignored)
  config.example.json      # source config template (merged over live config.json)
  config.json              # gitignored — holds Reddit creds (via `setup`)
  trawl.db                 # gitignored SQLite pool
  Trawl.vbs launch.ps1 run.ps1 install-shortcut.ps1   # Windows desktop launch chain
```

---

## 4. Data model & state

- **`trawl.db` (SQLite)** — the item pool. Each row is a scraped item with a `kind`
  (`business` | `ai-feature`), title/body/url/source/score, and dedupe/seen/posted flags.
  `store.connect()` runs idempotent `ALTER TABLE ADD COLUMN` migrations (CREATE-IF-NOT-EXISTS skips
  existing tables, so new columns are added defensively). `store.pool(kind, n)` draws **at random**.
- **`config.json` (gitignored)** — merged over `config.example.json` at load, so new default keys
  appear automatically without re-copying. Holds Reddit OAuth creds (entered via `setup`, never
  echoed). **Never committed.**
- **The `Posts/` folder lives OUTSIDE the repo** — in the Ship Something Real vault
  (`…\AI-Beginner-Business\Posts\`), pointed at by `posts_dir` in config. One folder per post:
  `post.md` (front-matter + hook + platform caption), `poc.html` (the built deliverable / screenshot
  source), optional `ad.mp4`/carousel `.pdf`. Plus `POSTING-LOG.md` (**source of truth for what's
  posted**), `approvals.json` (soft approve/reject signal), and `requests/` (queued generation jobs).

---

## 5. The pipeline

```
scrape (business + ai-feature)         sources.py
   → clean/dedupe gates                rank.py (drops junk/off-topic; ranking retired)
   → pool (SQLite, random draw)        store.py
   → generate by TYPE                  idea.py  (classify → hook → mode → spec)
   → build prompt                      prompt.py (design bar + honesty guards)
   → [Claude builds the deliverable]   (single-file HTML / working PoC / ad / carousel)
   → review board                      board.py (approve, copy caption, mark posted)
   → post (assisted, human sends)      → the funnel (Kit newsletter + free gift)
```

### Post types (`idea.POST_TYPES`)
| Type | Pool kind | Mode | Deliverable |
|---|---|---|---|
| `linkedin` | business | educational | editorial single-file "teaching figure" + caption, **no gate**, signed |
| `twitter` | business | promo | outcome-first cover + funnel caption (reply-keyword) |
| `aifeature` | ai-feature | showcase | explainer card for a real model/tool/paper, educational |
| `ad` | business | ad | finished ad creative (see §7) |
| `poc` | business | poc | a **working** single-file app, saved to the vault POC-Library |
| carousel | (authored) | — | multi-slide LinkedIn **PDF** document post (see §8) |

### Honesty guards (enforced in prompts + tests)
Gatekeep the "how" (sell judgement, name no tools/steps); **never fabricate income figures or
present invented statistics as research** (`$\d{3,}` in a promo hook is a test failure); no fake
charts. A number on a cover is a plausible illustration, never a cited finding.

---

## 6. Module reference (core content path)

- **`sources.py`** — one fetcher per source, all `urllib`. HN via Algolia (`numericFilters` +
  365-day cutoff), Stack Exchange (free, 300/day, gunzip), Reddit (app-only OAuth `client_credentials`,
  read-only), and the AI-feature "feelers" (Hugging Face trending models, recent GitHub AI repos,
  latest arXiv cs.AI/LG/CL). Also `fetch_ideas` upserts curated `Ideas-Bank/ideas.jsonl` entries as
  pool items (bypassing the junk gate). **LinkedIn is never scraped** (ToS + account risk) — manual
  paste lane instead.
- **`rank.py`** — formerly ranked; now **cleaning-only**. Title-only drop gates remove junk/off-topic;
  junk is a ×0.25 penalty, never a hard drop for audience-relevant tokens; every pattern is
  word-boundary matched. Regression-guarded by `tests/test_gates.py`.
- **`idea.py`** — `generate(items, type, n)`: shuffle the pool, and for each item `classify()`
  (topic → business category → concrete artifact → CTA keyword via the ordered `ARTIFACT_RULES`
  table), pick a **proof angle** (`ANGLES`), and choose a non-repeating **hook** from per-mode banks
  (gated/free/promo/educational/ad/poc/ai-feature). Deterministic (sha1). Emits a spec dict.
- **`prompt.py`** — turns a spec into the **build prompt** Claude receives. Five templates
  (`IDEA/AIFEATURE/LINKEDIN_EDU/AD/POC`), each carrying the platform caption voice, the funnel plan,
  the honesty guard, and the **design bar** (the measured Linear/Stripe/Notion/Vercel/Superhuman/
  Intercom/Raycast rules — radius 8/12/16, weight ≤600, one accent, hairlines, system fonts under a
  strict CSP; full text in `docs/output-design-rules.md`). Prepends `LIBRARY_NOTE` ("read
  `Posts/LIBRARY.md`, reference never copy"). An operator "direction note" steers subject/tone
  without breaking type rules.
- **`library.py`** — digests every post folder into `Posts/LIBRARY.md` + `library.json` (coverage,
  per-post hook/caption/design fingerprint, reusable-PoC shelf). Auto-refreshed on queue.
- **`store.py`** — SQLite access + migrations + the random `pool()` draw.

---

## 7. Ad engine (`ad.py` + `render.py`)

Turns a brief into a **static or motion ad** under the **two-layer law**: readable text (headline/
CTA/brand) is ALWAYS a crisp HTML/SVG overlay, **never diffused** by an image model.

- **`ad.py`** — the *type layer*. Encodes the Creatify matrix (16 angles, angle→visual-direction map,
  headline formulas, platform/aspect matrix, brand tokens). Renders an animated dark-editorial HTML
  ad, captures **one PNG per frame** by freezing the CSS animation at increasing
  `--virtual-time-budget` (via `shot.py`), and stitches with `ffmpeg`. Three speed **modes**:
  `fast` (type only, no GPU) / `still` (AI image behind type) / `motion` (Wan video b-roll).
- **`render.py`** — the *visual layer*. `compile_prompt` builds a model-grade prompt (natural vs
  tag family + brand `palette_words`); `render_still` reuses Z-Image Turbo on local **ComfyUI**;
  `render_broll` runs **Wan 2.2** t2v (recipe: subject→motion→ONE camera move→light→texture; strict
  negatives); `verify_still` is a best-effort, non-blocking **Florence-2** gate via PromptLens.
- Motion ads composite the transparent type frames over the b-roll with `ffmpeg` alpha overlay.
  Long renders run in a **background thread** with progress/cancel (see `cli.py` `/api/make_ad`).

---

## 8. Carousel generator (`carousel.py`)

Spec (cover + slides + CTA) → a LinkedIn **"document post" PDF**. Renders a multi-page HTML (one
print page per slide, `@page` sized 1080×1350 4:5, `print-color-adjust:exact` so dark backgrounds
survive) and drives the browser's **`--print-to-pdf`** for **vector-crisp** text — no image encoding,
no new deps (reuses `shot.find_browser()`). Design law: **BIG + readable** — huge bold sans headings,
one idea per slide, top bar (brand + pill), cover "Inside" list, page counters, footer meta; light
theme default, dark available. Additive: new module + a `carousel` CLI verb; nothing else changed.
`python trawl.py carousel [spec.json]`.

---

## 9. Server, UI & desktop app

- **`cli.py`** hosts the `ThreadingHTTPServer`. Routes: `/` (review board), `/console` (legacy raw
  generator), `/api/generate`, `/api/queue_generate`, `/api/posts`, `/api/requests`, `/api/mark`,
  `/api/post`, `/api/make_ad` + `/api/ad_status` + `/api/cancel_ad`, `/api/library`, `/api/stats`,
  `/poc/<slug>`, `/vid/<slug>`, `/api/ping`. A **watchdog** self-exits after ~45s without a UI
  heartbeat, so closing the window leaves no orphan process.
- **`board.py`** is the whole dashboard as one HTML string (B&W elevated-monochrome design):
  **Generate** tab (type picker + direction note + count; motion-ad card with speed modes + live
  progress) and **Review** tab (per-post PoC iframe / `<video>`, copy-caption, assisted Post-to-
  LinkedIn/X, approve/reject, mark-posted). Two stores kept separate: `approvals.json` (soft signal)
  and `POSTING-LOG.md` (truth). Posting is **assisted, never autonomous** (no free post API;
  automating a logged-in session risks the account).
- **Desktop launch chain:** `Trawl.vbs` (window style 0, no console) → `launch.ps1 -Silent` →
  **`pythonw.exe`** (a genuinely windowless binary — *not* a hidden console; this distinction is
  load-bearing) → opens a Chrome/Edge tab. `install-shortcut.ps1` builds the shortcut + a real
  multi-size `trawl.ico`; `run.ps1` is the visible-console debug path.

### CLI surface (`python trawl.py <cmd>`)
`app` (desktop server + watchdog) · `serve` (server, opens browser) · `fetch` (scrape+store) ·
`setup` (Reddit OAuth creds) · `library` (rebuild notes index) · `top -n` (print pool) ·
`ad [brief.json] [--note …] [--to-posts]` · `carousel [spec.json]` · `shot <url|file>` (WIP).

---

## 10. Testing & conventions

- **No pytest** — stdlib `assert` scripts: `tests/test_gates.py` (16 good items survive, 10 bad
  dropped/penalised — guards the cleaning regexes against real data) and `tests/test_idea.py`
  (deterministic generate() contract + keyword + no-income-figure guard). Both currently PASS.
- **Module self-checks:** `ad.py selftest`, `carousel.py selftest`, `render.py --selftest`,
  `shot.py demo` — each verifies its layer without needing the browser/GPU where possible.
- **Coding conventions:** stdlib-only (a new dependency is a design smell); additive modules + CLI
  verbs (don't touch the existing generate/review path); single-file/CSP-safe output; deterministic
  where a feed must be plan-able; tuning tables live at the top of each module; measure regexes
  against real corpus data, never review them by eye.
- **Recurring lessons banked:** kill stray `python`/`pythonw` before debugging "impossible" issues;
  use `pythonw.exe`, don't hide a console; CSS theme values live in ~4 blocks (`:root`, dark
  `@media`, `[data-theme]` ×2) — change all four; `--virtual-time-budget` freezes CSS animation at
  time *t* (the frame-capture trick).

---

## 11. Source control & maintenance (GitHub)

- **Remote:** `origin → https://github.com/spitnik11/trawl.git` (**private**), default branch
  `master`, local tracks `origin/master`.
- **Authorship rule:** all commits authored as **Gabriel Pina** — **no Claude co-author** trailer.
  A global **secret-blocking pre-commit hook** (`~/.claude/git-safety/`) guards against committing
  credentials. All repos private.
- **`.gitignore`** covers `config.json`, `trawl.db`, `__pycache__/`, `*.pyc`, `out/`, `err.txt`.
- **Current working-tree state (2026-08-06):** uncommitted local WIP — `core/board.py` and
  `core/cli.py` modified (a `shot` command + extra routes) and `core/carousel.py` new/untracked. So
  `origin/master` is a few commits behind local; the carousel generator and `shot` verb are not yet
  pushed. Commit history is small and squash-clean (initial commit → dir reorg → dead-code removal →
  move modules into `core/`).
- **Maintenance model:** solo, AI-assisted. Trawl is edited in-place (often with an assistant), run
  locally, and pushed to the private GitHub repo. No CI, no releases, no external contributors —
  by design (zero-dependency, single-user tool).

---

## 12. Known gaps / roadmap

- **Reddit access is gated** — app creation no longer grants API access; OAuth tokens are manually
  approved (2–4 wks). Stack Exchange is the working substitute; Reddit remains the real route to the
  business audience. Run `python trawl.py setup` when approved.
- **Ad engine next:** batch/variants, brand-swap, aspect fan-out, duration control, auto-post.
- **Carousel:** optionally export slide PNGs alongside the PDF (LinkedIn image-carousel fallback).
- **Uncommitted WIP** (board/cli `shot` + carousel) should be reviewed and pushed.
- **Feedback loop:** generation is currently blind to post performance; a "what got engagement →
  bias future hooks/topics" loop is the biggest quality upgrade.

---

## 13. How Trawl fits the business

Trawl is the **content engine** for the Ship Something Real funnel (see `PROJECT-ShipSomethingReal`):
LinkedIn posts/carousels (Trawl-generated) → the live portfolio + a **Kit** free-guide page → email
list → weekly newsletter (Trawl's AI-feature feelers draft the roundup) → eventual workflow-kit
sales. The posting+design knowledge is also extracted into reusable Claude skills (`trawl-post`,
`trawl-design`, `trawl-ad`) so the same rules run outside the app.
