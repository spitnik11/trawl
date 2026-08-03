"""Data sources. Standard library only — urllib + json.

Reddit  : OAuth app-only (client_credentials). Public JSON is 403-blocked, verified 2026-07-23.
HN      : Algolia search API, no auth required. Verified working.
Manual  : paste lane for LinkedIn and anywhere else that must not be scraped.
"""

import gzip
import html
import json
import re
import time
import base64
import urllib.request
import urllib.parse
import urllib.error

TIMEOUT = 25

_TAG = re.compile(r"<[^>]+>")


def fetch_ideas(cfg):
    """Read curated content ideas from the vault Ideas-Bank (JSONL) as pool items.

    These are hand-curated seeds, not scraped — so trawl.do_fetch upserts them directly, bypassing
    the junk-cleaning gate, and they persist in the pool for generation to draw on. Path is
    `ideas.path` in config. Each line: {id,title,note,source,audience,tags,added}.
    """
    path = cfg.get("path")
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    now = time.time()
    items = []
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith("//"):
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        title = (d.get("title") or "").strip()
        if not title:
            continue
        iid = d.get("id") or re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
        items.append({
            "id": f"ideas:{iid}",
            "source": "ideas",
            "title": title,
            "body": d.get("note", ""),
            "url": d.get("source", ""),
            "score": 0, "comments": 0,
            "created_utc": now,
            "kind": "business",
            "audience": d.get("audience", "business"),
            "funnel_asset": (d.get("artifact") or "").strip(),  # idea's own concrete artifact
        })
    return items


def clean_text(s):
    """Strip HTML tags and unescape entities. HN story_text is HTML, not plain text."""
    if not s:
        return ""
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _get_json(url, headers=None, data=None, method="GET"):
    req = urllib.request.Request(url, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if data is not None:
        req.data = urllib.parse.urlencode(data).encode()
        req.method = "POST"
    req.add_header("Accept-Encoding", "gzip")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
            # Stack Exchange always gzips, regardless of what you ask for.
            if r.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} from {url.split('?')[0]}: {e.read()[:200]!r}") from e
    except Exception as e:
        raise RuntimeError(f"request failed for {url.split('?')[0]}: {e}") from e


# --------------------------------------------------------------------------- Reddit

_token_cache = {"token": None, "expires": 0}


def reddit_token(cfg):
    """App-only OAuth. Never sees your Reddit password; read-only access."""
    if _token_cache["token"] and time.time() < _token_cache["expires"] - 60:
        return _token_cache["token"]

    cid = cfg.get("client_id", "").strip()
    secret = cfg.get("client_secret", "").strip()
    if not cid or cid.startswith("PASTE"):
        raise RuntimeError("no Reddit credentials yet — run: python trawl.py setup")

    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    payload = _get_json(
        "https://www.reddit.com/api/v1/access_token",
        headers={
            "Authorization": f"Basic {basic}",
            "User-Agent": cfg.get("user_agent", "trawl/0.1"),
        },
        data={"grant_type": "client_credentials"},
    )
    _token_cache["token"] = payload["access_token"]
    _token_cache["expires"] = time.time() + payload.get("expires_in", 3600)
    return _token_cache["token"]


RATE_DELAY = 0.6   # Reddit allows ~100 requests/minute on OAuth. Stay under it.


def _subreddit_list(cfg):
    """Accepts either a flat list or {audience: [subs]}. Returns [(sub, audience)]."""
    subs = cfg.get("subreddits", [])
    if isinstance(subs, dict):
        return [(s, aud) for aud, lst in subs.items() for s in lst]
    return [(s, "builder") for s in subs]


def _reddit_posts(data, sub, audience):
    out = []
    for child in data.get("data", {}).get("children", []):
        d = child.get("data", {})
        if d.get("stickied") or d.get("over_18"):
            continue
        out.append({
            "id": f"reddit:{d.get('id')}",
            "source": f"r/{sub}",
            "audience": audience,
            "title": clean_text(d.get("title")),
            "body": clean_text(d.get("selftext"))[:4000],
            "url": "https://www.reddit.com" + d.get("permalink", ""),
            "score": int(d.get("score", 0)),
            "comments": int(d.get("num_comments", 0)),
            "created_utc": float(d.get("created_utc", 0)),
        })
    return out


def fetch_reddit(cfg):
    """Three passes per subreddit: top listing, phrase searches, and recent comments.

    The phrase searches are the point. Asking Reddit directly for "how can i" inside
    r/smallbusiness returns the exact question shape we want, instead of pulling the whole
    top listing and hoping the filters catch something.
    """
    if not cfg.get("enabled"):
        return []
    token = reddit_token(cfg)
    ua = cfg.get("user_agent", "trawl/0.1")
    hdr = {"Authorization": f"Bearer {token}", "User-Agent": ua}

    listing = cfg.get("listing", "top")
    timeframe = cfg.get("timeframe", "month")
    limit = int(cfg.get("limit_per_sub", 50))
    queries = cfg.get("search_queries", [])
    search_limit = int(cfg.get("search_limit", 40))
    want_comments = bool(cfg.get("include_comments", True))
    comment_limit = int(cfg.get("comment_limit", 100))

    out = []
    for sub, audience in _subreddit_list(cfg):
        before = len(out)
        q_sub = urllib.parse.quote(sub)

        # 1. top listing
        try:
            out += _reddit_posts(_get_json(
                f"https://oauth.reddit.com/r/{q_sub}/{listing}"
                f"?t={timeframe}&limit={limit}&raw_json=1", headers=hdr), sub, audience)
            time.sleep(RATE_DELAY)
        except RuntimeError as e:
            print(f"  ! r/{sub} listing: {e}")

        # 2. phrase searches — "how can i", "what do you use", ...
        for q in queries:
            try:
                out += _reddit_posts(_get_json(
                    f"https://oauth.reddit.com/r/{q_sub}/search"
                    f"?q={urllib.parse.quote(q)}&restrict_sr=1&sort=top&t={timeframe}"
                    f"&limit={search_limit}&raw_json=1", headers=hdr), sub, audience)
                time.sleep(RATE_DELAY)
            except RuntimeError as e:
                print(f"  ! r/{sub} search '{q}': {e}")

        # 3. recent comments — where the real questions often live
        if want_comments:
            try:
                data = _get_json(
                    f"https://oauth.reddit.com/r/{q_sub}/comments"
                    f"?limit={comment_limit}&raw_json=1", headers=hdr)
                out += _reddit_comments(data, sub, audience)
                time.sleep(RATE_DELAY)
            except RuntimeError as e:
                print(f"  ! r/{sub} comments: {e}")

        print(f"  r/{sub} [{audience}]: +{len(out) - before}")
    return out


def _reddit_comments(data, sub, audience):
    """Comments become items in their own right. Title = the question sentence."""
    out = []
    for child in data.get("data", {}).get("children", []):
        d = child.get("data", {})
        body = clean_text(d.get("body"))
        if not body or len(body) < 25 or d.get("author") == "AutoModerator":
            continue
        out.append({
            "id": f"reddit:{d.get('id')}",
            "source": f"r/{sub} (comment)",
            "audience": audience,
            "title": _question_sentence(body),
            "body": body[:4000],
            "url": "https://www.reddit.com" + d.get("permalink", ""),
            "score": int(d.get("score", 0)),
            "comments": 0,
            "created_utc": float(d.get("created_utc", 0)),
        })
    return out


_QSENT = re.compile(
    r"([^.!?\n]*\b(?:how (?:can|do|would|should) (?:i|you|we)|what (?:do|would|are) you|"
    r"what'?s the best|any(?:one|body) (?:know|else)|is there a way|"
    r"has anyone|can someone|what should i)\b[^.!?\n]*\?)",
    re.I,
)


def _question_sentence(body):
    """Pull the actual question out of a comment; fall back to the opening line.

    A comment has no title, and using the first 120 characters produced garbage titles like
    "Yeah I had the same issue last year, what I ended up doing was". The question sentence
    is the part worth ranking and the part the prompt generator needs.
    """
    m = _QSENT.search(body)
    if m:
        return m.group(1).strip()[:200]
    first = re.split(r"(?<=[.!?])\s+", body.strip())[0]
    return first[:160]


# --------------------------------------------------------------------------- Hacker News


def fetch_hn(cfg):
    """Algolia search. No auth, no key. Verified working 2026-07-23."""
    if not cfg.get("enabled"):
        return []
    per = int(cfg.get("hits_per_query", 30))
    # Algolia defaults to all-time. Without this, a 5-year-old post with huge engagement
    # outranks everything current. Verified: a 62-month-old story took rank #1.
    max_age_days = int(cfg.get("max_age_days", 180))
    cutoff = int(time.time() - max_age_days * 86400)

    out = []
    for q in cfg.get("queries", []):
        url = (
            "https://hn.algolia.com/api/v1/search"
            f"?query={urllib.parse.quote(q)}&tags=story&hitsPerPage={per}"
            f"&numericFilters=created_at_i%3E{cutoff}"
        )
        try:
            data = _get_json(url, headers={"User-Agent": "trawl/0.1"})
        except RuntimeError as e:
            print(f"  ! hn '{q}': {e}")
            continue

        for h in data.get("hits", []):
            if not h.get("title"):
                continue
            out.append({
                "id": f"hn:{h.get('objectID')}",
                "source": "hackernews",
                "title": clean_text(h["title"]),
                "body": clean_text(h.get("story_text"))[:4000],
                "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                "score": int(h.get("points") or 0),
                "comments": int(h.get("num_comments") or 0),
                "created_utc": float(h.get("created_at_i") or 0),
            })
        print(f"  hn '{q}': {len(out)} cumulative")
    return out


# --------------------------------------------------------------------------- Stack Exchange


def fetch_stackexchange(cfg):
    """Free, no auth, no approval queue. Every item is a question by construction.

    Added after Reddit's 2026 gating pushed new OAuth tokens behind manual approval. This is
    the closest legitimate substitute for the builder audience — it does NOT cover the
    business audience, which still needs Reddit.

    Quota is 300 requests/day unauthenticated, which is plenty at this volume.
    """
    if not cfg.get("enabled"):
        return []
    per = int(cfg.get("per_query", 25))
    timeframe_days = int(cfg.get("max_age_days", 365))
    from_date = int(time.time() - timeframe_days * 86400)

    # (site, audience, queries) — business sites get their own phrasing
    plan = [(s, "builder", cfg.get("queries", [])) for s in cfg.get("sites", [])]
    plan += [(s, "business", cfg.get("business_queries", []))
             for s in cfg.get("business_sites", [])]

    out = []
    for site, audience, queries in plan:
        before = len(out)
        for q in queries:
            url = (
                "https://api.stackexchange.com/2.3/search/advanced"
                f"?order=desc&sort=votes&q={urllib.parse.quote(q)}"
                f"&site={urllib.parse.quote(site)}&pagesize={per}"
                f"&fromdate={from_date}&filter=withbody"
            )
            try:
                data = _get_json(url, headers={"User-Agent": "trawl/0.1"})
            except RuntimeError as e:
                print(f"  ! se/{site} '{q}': {e}")
                continue

            for h in data.get("items", []):
                if not h.get("title"):
                    continue
                out.append({
                    "id": f"se:{site}:{h.get('question_id')}",
                    "source": f"stackexchange/{site}",
                    "audience": audience,
                    "title": clean_text(h["title"]),
                    "body": clean_text(h.get("body"))[:4000],
                    "url": h.get("link", ""),
                    "score": int(h.get("score") or 0),
                    # answers are the discussion signal here, equivalent to comments
                    "comments": int(h.get("answer_count") or 0),
                    "created_utc": float(h.get("creation_date") or 0),
                })
            if data.get("quota_remaining") is not None and data["quota_remaining"] < 20:
                print(f"  ! stackexchange quota low: {data['quota_remaining']} left today")
        print(f"  se/{site} [{audience}]: +{len(out) - before}")
    return out


# --------------------------------------------------------------------------- AI-news feelers
# These pull "what's new in AI" — models, tools, research — to feed the ai-feature post type.
# All free, no auth. Items are tagged kind="ai-feature" so the generator can target them.


def fetch_huggingface(cfg):
    """Trending models on Hugging Face. The freshest 'new AI model' signal there is."""
    if not cfg.get("enabled"):
        return []
    limit = int(cfg.get("limit", 20))
    try:
        rows = _get_json(
            f"https://huggingface.co/api/models?sort=trendingScore&limit={limit}&full=false",
            headers={"User-Agent": "trawl/0.1"})
    except RuntimeError as e:
        print(f"  ! huggingface: {e}")
        return []
    out = []
    for m in rows:
        mid = m.get("id", "")
        if not mid:
            continue
        task = (m.get("pipeline_tag") or "").replace("-", " ")
        out.append({
            "id": f"hf:{mid}",
            "source": "huggingface",
            "kind": "ai-feature",
            "audience": "ai",
            "title": mid.split("/")[-1].replace("-", " "),
            "body": f"{mid} — {task} model, {m.get('downloads', 0):,} downloads. Trending on Hugging Face.",
            "url": f"https://huggingface.co/{mid}",
            "score": int(m.get("likes") or 0),
            "comments": 0,
            "created_utc": time.time(),
        })
    print(f"  huggingface: {len(out)}")
    return out


def fetch_github(cfg):
    """Recently-created, fast-rising AI repos. New tools/agents to show off."""
    if not cfg.get("enabled"):
        return []
    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - int(cfg.get("since_days", 45)) * 86400))
    per = int(cfg.get("limit", 15))
    q = urllib.parse.quote(f"{cfg.get('query', 'ai agent')} created:>{since}")
    try:
        data = _get_json(
            f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page={per}",
            headers={"User-Agent": "trawl/0.1", "Accept": "application/vnd.github+json"})
    except RuntimeError as e:
        print(f"  ! github: {e}")
        return []
    out = []
    for r in data.get("items", []):
        out.append({
            "id": f"gh:{r.get('id')}",
            "source": "github",
            "kind": "ai-feature",
            "audience": "ai",
            "title": r.get("name", "").replace("-", " "),
            "body": clean_text(r.get("description"))[:600],
            "url": r.get("html_url", ""),
            "score": int(r.get("stargazers_count") or 0),
            "comments": int(r.get("open_issues_count") or 0),
            "created_utc": time.time(),
        })
    print(f"  github: {len(out)}")
    return out


def fetch_arxiv(cfg):
    """Latest AI papers. The research edge of 'what's new' — good for educational posts."""
    if not cfg.get("enabled"):
        return []
    import xml.etree.ElementTree as ET
    cats = "+OR+".join(f"cat:{c}" for c in cfg.get("categories", ["cs.AI", "cs.LG"]))
    n = int(cfg.get("limit", 15))
    url = (f"http://export.arxiv.org/api/query?search_query={cats}"
           f"&sortBy=submittedDate&sortOrder=descending&max_results={n}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "trawl/0.1"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            xml = r.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"  ! arxiv: {e}")
        return []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in ET.fromstring(xml).findall("a:entry", ns):
        title = clean_text(e.findtext("a:title", "", ns))
        summ = clean_text(e.findtext("a:summary", "", ns))
        link = e.findtext("a:id", "", ns)
        if not title:
            continue
        out.append({
            "id": f"arxiv:{link.rsplit('/', 1)[-1]}",
            "source": "arxiv",
            "kind": "ai-feature",
            "audience": "ai",
            "title": title,
            "body": summ[:600],
            "url": link,
            "score": 0,
            "comments": 0,
            "created_utc": time.time(),
        })
    print(f"  arxiv: {len(out)}")
    return out


