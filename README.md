# Trawl

Finds what people are actually struggling with around Claude/AI building, ranks it, and hands
you a ready-to-paste build prompt.

**The pipeline:** scrape → score → pick → prompt → build with Claude → screenshot → post → funnel.

Trawl does the first four. You and Claude do the rest.

---

## Why it exists

Content dies from "what do I post today." Trawl answers that with evidence instead of instinct:
real questions, real engagement numbers, ranked. Then it writes the prompt so there's no blank
page between "good idea" and "built thing."

Every idea it surfaces is tagged with which part of the **Ship Something Real** funnel it feeds,
so no post is ever orphaned from a call to action.

---

## Requirements

- **Python 3.10+** (3.10.11 confirmed on this machine)
- **Zero pip installs.** Standard library only — `urllib`, `sqlite3`, `http.server`, `json`.

That's deliberate. No dependencies means nothing to break, no virtualenv, and it still runs in
two years.

---

## Setup

### 1. Hacker News + Stack Exchange — work immediately, no setup

No key, no account, no approval queue. Both tested and returning live results.

**Stack Exchange is the workhorse.** Every item on it is a question by construction, which is the
exact shape Trawl ranks for, and it carries vote + answer counts as engagement signal. Sites used:
`stackoverflow` and `softwareengineering`.

> `superuser` was tried and removed — it's a tech-support site, so it returns "how do I fix
> Notepad++" rather than "how do I learn to build". Wrong kind of question.

Keep the Stack Exchange `max_age_days` aligned with `MAX_AGE_DAYS` in `rank.py` (both 365).
They were briefly 730 vs 365, which burned quota fetching 127 items the ranker then discarded.

Unauthenticated quota is 300 requests/day — far more than Trawl needs.

### 2. Reddit — gated as of 2026, apply early

**Status: not connected, and getting connected is no longer instant.** Reddit blocks
unauthenticated JSON (verified: `403 Blocked`), and as of 2026 creating an app at
`prefs/apps` no longer grants access on its own — new OAuth tokens are **manually approved**
under the Responsible Builder Policy, reportedly 2–4 weeks. Commercial use is
**$0.24 per 1,000 calls**; at Trawl's volume (~60 requests per fetch) that's roughly
**$1.30/month**, which is the clean answer for a tool feeding a business.

Dead ends already checked, so nobody re-checks them:

| Route | Result |
|---|---|
| `reddit.com/r/x/.json` | `403 Blocked` |
| Reddit RSS (`/.rss`) | `429 Too Many Requests` |
| libreddit | Archived since July 2023 |
| Redlib | Works by emulating an official client — circumvention, and a fragile HTML scrape |
| Devvit | Wrong architecture entirely: apps run **on Reddit's servers inside a subreddit**, so they cannot feed a local pipeline. The Builder Policy also forbids masking how or why you access data. |

**Reddit is still the only route to the business audience — this was tested, not assumed.**

Stack Exchange business sites were tried. `freelancing` and `webmasters` are enabled and cost
8 requests, but the measured yield was **9 items fetched, 0 surviving the filters**: their
top-voted content is client-relationship discussion ("Dealing with a Client with Bad Priorities"),
which is correctly dropped as *inert* — not a question, not a concrete build. `workplace` was
tested and **rejected outright**: high vote counts (347v, 260v) on HR and career drama, which is
exactly the high-engagement-junk pattern the ranker was rebuilt to stop.

Start the Reddit application now so the clock runs while you build content.

The setup command below works unchanged the moment credentials are approved.

```powershell
cd "Z:\Claude app\trawl"; python trawl.py setup
```

It walks you through registering the app, then **prompts for the keys locally and writes them
straight to `config.json`** — the secret is typed via `getpass`, so it is never echoed to the
screen, never printed, and never appears in shell history. It then tests the credentials and
tells you if they work.

What it asks you to do, if you'd rather do it by hand:

1. https://www.reddit.com/prefs/apps → **create another app...**
2. Name `trawl`, type **script**, redirect URI `http://localhost:8080`
3. Client ID is the short string under the app name; secret is labelled "secret"

> **App-only OAuth (`client_credentials`).** Trawl never asks for your Reddit password and
> cannot post, vote, or read anything private. Read-only access to public listings.

`config.json` is gitignored. Never commit it, never paste it anywhere.

### What Reddit pulls — three passes per subreddit

| Pass | What |
|---|---|
| **Listing** | Top posts for the timeframe |
| **Phrase search** | Asks Reddit directly for `"how can i"`, `"how do i"`, `"what do you use"`, `"is there a way"` inside each sub — targets the question shape rather than pulling everything and hoping |
| **Comments** | Recent comments become items in their own right. The real question is often a reply, not a post. A comment's "title" is the question sentence extracted from it, not the first 120 characters. |

Requests are spaced 0.6s apart to stay under Reddit's ~100/minute OAuth limit.

### Two audiences

Subreddits are grouped, and the grouping changes the generated prompt:

| Audience | Subs | Prompt angle |
|---|---|---|
| **builder** | ClaudeAI, ChatGPTCoding, learnprogramming, SideProject, nocode | Teach the method — maps to a specific guide or project |
| **business** | smallbusiness, Entrepreneur, SaaS, solopreneur, freelance | Before/after board: the manual way and its time cost vs the automated way |

**A strategic note worth being explicit about.** The business audience has money, which is why
it's worth mining — but they want the problem *solved*, not to learn to build. They are not
buyers of a beginner's build-along book. Business items are therefore tagged *"Proof content —
shows what AI can build"*: they attract attention and demonstrate capability, and the people who
convert are the onlookers who want to learn to do it themselves. If business-audience posts start
outperforming, that's a signal to add a done-for-you service, not to repoint the book at them.

### 3. LinkedIn — manual, by design

Not scraped. LinkedIn's User Agreement prohibits automated collection, they actively block it,
and the feed requires your authenticated session — so scraping risks the account this business
depends on.

Instead: hit **+ Paste** in the UI, drop in a post or comment thread you saw, and it enters the
same scoring pipeline as everything else. Two seconds of copy-paste, zero account risk.

---

## Running it

**Double-click `Trawl` on your Desktop.** No console, no terminal, no browser chrome.

If the shortcut isn't there yet:

```powershell
cd "Z:\Claude app\trawl"; .\install-shortcut.ps1
```

### How the desktop app works

Three moving parts, all standard library or already on Windows:

- **`Trawl.vbs`** launches PowerShell at window style 0. A shortcut pointing at
  `powershell.exe` shows a console for the life of the script, and `-WindowStyle Hidden` on the
  shortcut still flashes one first. The VBS shim shows nothing at all — and because there's then
  no console to print to, `launch.ps1 -Silent` reports failures through a dialog instead.
- **`pythonw.exe`** runs the backend. It's a genuinely windowless binary, *not* a console app
  hidden with `-WindowStyle Hidden` — that distinction matters and is the reason nothing flickers.
- **Edge/Chrome `--app=` mode** gives a chromeless window with its own taskbar entry. It looks
  like a native app and costs zero dependencies, because Edge ships with Windows.

**Closing the window shuts the backend down.** The UI sends a heartbeat every 10s; after 45s of
silence the server exits on its own. No orphaned Python process, ever. Verified by test.

### Using it

A ranked list, highest first. Click any row to see its build prompt.

1. Scan the list — rank, source, engagement, theme, score bar
2. Click a row → the build prompt appears on the right
3. **copy prompt** → paste to Claude
4. **mark used** so it drops out of the list

**sort: rank** is the default descending ranking. **sort: random** shuffles the same set, for
when the top of the list feels stale.

The list auto-refreshes every 30s and pulls fresh data in the background when it's over 6h old.

Data refreshes automatically when it's more than 6 hours old, in the background, without
blocking the window. **Refresh** forces it.

### Terminal fallback (debugging only)

```powershell
.\run.ps1                 # visible console, opens in a normal browser tab
python trawl.py fetch     # pull + score only
python trawl.py top -n 10 # print rankings
TRAWL_IDLE=8 python trawl.py app   # shorter idle-shutdown, for testing
```

---

## How ranking works

Not raw upvotes. The formula weights **comments more heavily than score**, because upvotes mean
agreement while comments mean unresolved pain — and unresolved pain is what content is for.

Two stages: **filter, then score.** Filtering matters more than scoring — an item that survives
to be ranked has already been judged to have something worth building from.

### Stage 1 — drop what can't produce a prompt

| Drop | When |
|---|---|
| Age | Older than 365 days |
| Thin | `score + 2×comments` under 6 |
| **Off-topic** | **Title** doesn't mention coding, AI, building, or a buildable artifact |
| **Inert** | Neither a question nor a concrete build — nothing for the prompt generator to use |

### Stage 2 — score what's left

| Signal | Weight | Why |
|---|---|---|
| Engagement | 45% | `log(score) + 1.5 × log(comments)`, percentile-ranked |
| Velocity | 25% | Points per hour |
| Recency | 30% | Exponential decay, ~1 week half-life |
| **Question bonus** | ×1.3 | Reads as a question or a plea for help |
| **Buildable bonus** | ×1.4 | Someone built a thing, or names a concrete visual artifact |
| **Junk penalty** | ×0.25 | One of 10 measured categories, **matched on title only** |
| **Novelty penalty** | ×0.3 | Already seen in a previous run |

**Junk is a penalty, never a drop**, and every gate that can drop an item reads the **title
only**. Both rules were learned the hard way — see below.

Tune the constants at the top of `rank.py`. Then run `python test_gates.py`.

---

## The audit that produced the current ranker (2026-07-23)

The first version emitted a build prompt for *"Thanks HN for 15 years of support and helping me
find my life's work"* — a company anniversary post. An independent audit measured the damage:
**66% junk in the top 50, 80% in the top 10.** Five root causes, each confirmed against the data:

1. **`BUILDABLE_PAT` fired on 92% of the corpus** — a ×1.4 applied to almost everything is a
   constant, not a signal. The token `ui` matched **119 times and never once as the word "UI"** —
   every hit was inside `built`, `building`, `quickly`. No word boundaries anywhere. Bare
   `built|build|made` also let narrative past tense qualify, which is precisely how an
   anniversary essay earned a buildable bonus.
2. **Bonuses scored against title + body, the penalty against title only.** Body text could only
   ever inflate a score, never deflate it. `NOISE_PAT` had **never fired once** across 89 items.
3. **Min-max normalisation made rank #1 an outlier artifact.** One 832-point post set the ceiling
   for both engagement and velocity, pinning itself at 1.0 and compressing the other 88 beneath
   it — 1.165 versus 0.612 for #2. Now percentile-ranked, so an outlier occupies one slot rather
   than the whole scale.
4. **`theme_of`'s title-doubling did nothing** — `re.search()` is boolean, so theme was decided
   purely by list order, and a body match beat a title match. 14 of the top 50 were mis-themed.
   Unanchored patterns matched `ship` inside *craftsmanship*, `free` inside *freelancers*,
   `live` inside *livelihood*, `bug` inside a changelog line.
5. **`showcase` was the junk bucket** because its pattern matched the literal `Show HN:` prefix
   carried by 35 of the top 50 — the config query `"Show HN built with ai"` was manufacturing a
   launch-announcement corpus.

### Then the correction over-corrected

A second audit measured the fix and found the opposite failure: of 16 unambiguously on-audience
titles, **12 were being silently deleted.**

- `My journey from zero coding experience to my first working app` → killed as *gratitude*
- `Ask HN: Docker wont start and I have no idea what any of this means` → killed as *infra*
- `I keep hitting the rate limit on the free tier, what do I do?` → killed as *industry-news*
- `Show HN: I made a dashboard for my running data` → failed the relevance gate

Three structural mistakes: junk matched against `body[:600]`, so one incidental word anywhere
killed an item; junk **dropped** rather than penalised, so there was no recovery from a single
false hit; and several tokens (`recipe`, `invoice`, `waitlist`, `docker`, `rate limit`,
`deprecated`) name things beginners legitimately build or break on. The drop counters read
`8 junk, 1 offtopic` — it looked like success.

Now: junk is a **×0.25 penalty**, every drop-capable gate reads the **title only**, and
`test_gates.py` locks in both directions with real titles.

### Where it landed

| | Junk in top 10 | Junk overall |
|---|---|---|
| v1 | 80% | 66% |
| v2 (over-tight) | 40% | 42% |
| v3 | ~20% | — |

`test_gates.py`: 16/16 good survive, 0 false drops, 0 junk unhandled.

**The lesson both audits share:** a regex that matches 92% of your corpus and one that deletes
75% of your best posts are equally invisible when you read the code. Neither shows up without
running real titles through it. That's what `test_gates.py` is for — run it after any pattern
change.

---

## What testing actually showed (2026-07-23)

Three findings from running it, not from reasoning about it:

1. **Industry drama dominates raw engagement.** The first run's top 5 were a source-code leak,
   telemetry accusations, and pricing changes — enormous engagement, worthless to beginners.
   Hence the noise penalty. It works: that entire category vanished from the rankings.

2. **HN Algolia defaults to all-time search.** A **62-month-old** post took rank #1 on pure
   engagement despite recency scoring 0.0. Fixed with a `numericFilters` date bound plus a hard
   age cutoff in `rank.py`. Re-verified: 120 items → 89, all current.

3. **HN is the wrong pond for this audience — accept it.** Even tuned, it returns YC launches
   and professional-developer debate, because that's who is there. One genuine beginner question
   surfaced in the top 10 (*"Ask HN: May be a basic question, but how can I use AI well?"*).

   **Reddit is the source that matters.** r/ClaudeAI, r/learnprogramming and r/SideProject are
   where "I know 0 coding and I asked Claude to build…" actually gets posted. The two-minute app
   registration is the highest-value setup step in this project — do it before relying on
   Trawl's output.

HN stays enabled as a secondary awareness feed. Just don't expect beginner pain from it.

---

## What you get out

Click any idea → a filled build prompt → **Copy**. Paste it to Claude. It specifies a
single-file, dependency-free, screenshot-in-one-frame artifact, because that's what posts well
and what your book teaches.

---

## Layout

```
trawl.py             entry point + local HTTP server
sources.py           Reddit (OAuth), Hacker News, manual paste
rank.py              scoring + theme buckets
prompt.py            build-prompt generation
store.py             SQLite (stdlib), dedupe, seen-tracking
ui/index.html        dashboard
config.json          your keys — gitignored, never commit
trawl.db             local data — gitignored
```
