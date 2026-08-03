"""Synthesizes post ideas from CLUSTERS of scored posts.

The pivot: rank.py finds one good post at a time, but a single post is a weak signal and a
weaker hook. What sells to a business audience is "lots of people are stuck on X, so I built
the thing that fixes X". So this module groups related posts, decides what concrete artifact
that group implies, and writes the hook line in the user's own voice.

Everything here is deterministic — no LLM, no `random`. The same input always renders the same
hooks, because a feed that reshuffles itself on every refresh is impossible to plan around.
Template choice is driven by a hash of the idea's own slug.

The tuning surface is the tables at the top, in this order of how often you'll touch them:
    ARTIFACT_RULES  — topic pattern -> business category, buildable artifacts, CTA keyword
    HOOK_TEMPLATES  — the voice
    STOPWORDS       — noise removal for the clustering
"""

import hashlib
import math
import random
import re
from collections import defaultdict

# Tokens that carry no topic. Includes the site prefixes ("ask hn", "show hn") and the
# question scaffolding that every title on these boards shares — without this, "how", "what"
# and "should" are the highest-frequency keywords in the corpus and every cluster is a
# cluster of questions rather than a cluster of subjects.
STOPWORDS = frozenset("""
a an and are as at be been being but by can cant could did do does doing done dont for from
get gets getting had has have having how i id if ill im in into is it its ive just like make
makes making may me might more most much must my need needs no nor not of off on once one only
or other our out over own same should so some such than that the their them then there these
they this those through to too try trying under until up use used using very want wants was we
well were what when where whether which while who why will with without would you your yours
ask show tell hn stackoverflow reddit post posts question questions answer answers advice help
anyone anybody someone everyone people best good better worst thing things stuff way ways
really actually still even ever never always maybe please thanks thank hi hello guys folks
new old first last next another also about after before again yet else lot lots bit little big
know knows think thought feel feels look looking looks going go goes went come comes came
start starts started keep keeps kept give gives given take takes took work works worked
year years month months week weeks day days time times today now
""".split()) | frozenset("""
currently find finds found necessary useful worth worthwhile avoid avoiding stop stopping
relying rely advice guidance suggestion suggestions recommend recommendation era age ages
grow growing joy struggle struggling wonder wondering aspiring moving forward quickly quick
faster fast slow skip skipping real properly wrong right strong weak simple easy hard difficult
needed wanted possible able unable sure else somebody nothing anything everything something
""".split())
# ^ Second block added after reading the first real run: these are generic verbs and
# adjectives that pass every frequency filter but describe no subject. They produced the
# clusters seeded on "currently", "find" and "necessary", each of which grouped four posts
# that had nothing in common except a filler word.

# Word-ish tokens. Keeps c++, c#, node.js intact — those are real topic signals and splitting
# them turned "c++" into "c", which then matched nothing.
TOKEN_PAT = re.compile(r"[a-z][a-z0-9+#.\-]*")
# HTML entities are decoded upstream in sources.py; this only strips leftover tags/urls that
# survive in Stack Exchange bodies.
CLEAN_PAT = re.compile(r"<[^>]+>|https?://\S+|`[^`]*`")

BODY_CHARS = 400         # same window rank.py reads. Long bodies drift off-topic.
W_TITLE_TOKEN = 3.0
W_TITLE_BIGRAM = 5.0     # a bigram ("landing page", "trading bot") is worth more than its parts
W_BODY_TOKEN = 1.0


# ---------------------------------------------------------------- the tuning table
#
# (category, pattern, artifacts, cta_keyword)
#
# ORDER MATTERS — first match wins, so specific business verticals come before the generic
# web/app buckets. Artifacts are written as bare noun phrases because the hook templates
# supply the article ("a"/"an") themselves.
#
# Every artifact must be a single screenshot-able thing a business owner would actually want.
# "a better workflow" is not an artifact. "an invoice generator" is.
#
# To extend: add a row, or add an artifact to an existing row. Nothing else needs to change.
ARTIFACT_RULES = [
    ("Finance & Trading", re.compile(
        r"\b(trading|trade[sr]?|stock[s]?|ticker|invest\w*|portfolio|dividend|backtest\w*|"
        r"crypto|bitcoin|hedge|broker\w*|margin|profit\w*|revenue|cash.?flow|payroll|"
        r"tax|accounting|bookkeep\w*|expense[s]?)\b", re.I),
     ("stock trading bot", "portfolio tracker", "dividend income calculator",
      "backtest dashboard", "profit margin calculator", "cash flow forecaster"),
     "MONEY"),

    ("Marketing & Sales", re.compile(
        r"\b(marketing|sales|lead[s]?|funnel|landing.?page|ad[s]?|advert\w*|copywrit\w*|"
        r"seo|newsletter|email.?list|brand\w*|audience|conversion|pitch|outreach|"
        r"cold.?email|pricing.?page|offer)\b", re.I),
     ("landing page that converts", "lead magnet", "pricing page", "ad copy generator",
      "cold email generator", "sales one-pager"),
     "LEADS"),

    ("Operations", re.compile(
        r"\b(invoic\w*|booking|book\s+a|schedul\w*|appointment[s]?|quote[s]?|estimate[s]?|"
        r"inventory|order[s]?|automat\w*|workflow[s]?|zapier|crm|onboard\w*|sop|"
        r"contract[s]?|checklist|admin|paperwork|spreadsheet.?hell)\b", re.I),
     ("invoice generator", "booking page", "instant quote calculator", "SOP dashboard",
      "client onboarding portal", "job scheduling board"),
     "AUTOMATE"),

    ("Data & Reporting", re.compile(
        r"\b(data|database[s]?|sql|postgres|mysql|sqlite|query|queries|spreadsheet[s]?|"
        r"excel|csv|report\w*|analytic[s]?|dashboard[s]?|chart[s]?|kpi|metric[s]?|"
        r"visuali[sz]\w*|stats?|statistics)\b", re.I),
     ("KPI dashboard", "monthly report generator", "spreadsheet replacement",
      "customer data dashboard", "live sales chart board"),
     "DATA"),

    ("Web presence", re.compile(
        r"\b(website[s]?|web.?site|html|css|front.?end|frontend|portfolio|domain|deploy\w*|"
        r"host\w*|server|wordpress|web.?page|web.?app|javascript|react|responsive|"
        r"live.?server)\b", re.I),
     ("business website", "portfolio site", "one-page brochure site",
      "services and pricing site", "before-and-after gallery site"),
     "SITE"),

    ("Customer Experience", re.compile(
        r"\b(chatbot[s]?|chat.?bot|support|customer[s]?|client[s]?|faq|helpdesk|ticket[s]?|"
        r"review[s]?|feedback|survey[s]?|form.?builder|dropdown)\b", re.I),
     ("customer support chatbot", "FAQ page that answers itself",
      "system that texts every customer for a review", "client feedback form with a live dashboard"),
     "CLIENTS"),

    ("Apps & Automation", re.compile(
        r"\b(app[s]?|mobile|android|ios|game[s]?|bot[s]?|agent[s]?|script[s]?|cli|tool[s]?|"
        r"integration[s]?|api|webhook[s]?)\b", re.I),
     ("internal tool that replaces a spreadsheet", "one-click report bot",
      "pricing calculator my customers use themselves", "daily summary bot"),
     "BUILD"),

    # Learning is the single biggest theme in the source data, and it's the one that needs
    # translating hardest: a business owner does not want "a tutorial", they want the thing
    # that proves the skill was worth buying. Every artifact here is an owner-facing object.
    ("Learning & AI", re.compile(
        r"\b(learn\w*|teach\w*|tutorial[s]?|course[s]?|study\w*|student[s]?|beginner[s]?|"
        r"newbie|fundamental[s]?|skill[s]?|practice|bootcamp|career|junior|self.?taught|"
        r"claude|chatgpt|gpt|copilot|cursor|llm[s]?|prompt[s]?|ai)\b", re.I),
     ("AI tool cost comparison board", "prompt library for my whole team",
      "30-day AI skills tracker", "AI-vs-agency cost calculator",
      "one-page AI action plan", "staff training tracker",
      "AI savings scoreboard"),
     "BUILD"),
]

# Used when nothing above matches. Deliberately still concrete — a vague fallback artifact
# produces a hook nobody can picture, which is worse than no post at all. Flagged as a
# non-confident match so `strength` reflects the guess.
FALLBACK = ("AI Builds",
            ("one-page business website", "simple client dashboard",
             "printable price list page", "one-page proposal generator"),
            "BUILD")

# What the proof-of-concept has to demonstrate, per category. One sentence, present tense.
ANGLES = {
    "Finance & Trading": "show the numbers moving on a single screen so the money case is obvious at a glance",
    "Marketing & Sales": "show the finished page and the offer in one frame, so it reads as something worth copying",
    "Operations": "show the manual version and the automated version side by side, with the hours saved stated as a number",
    "Data & Reporting": "turn a messy spreadsheet into one clean board that answers the owner's question in three seconds",
    "Web presence": "show the whole page in one screenshot so it's clear a real business could ship it tomorrow",
    "Customer Experience": "show a real customer interaction resolving itself end to end, with no staff involved",
    "Apps & Automation": "show the tool doing one useful job start to finish, small enough to grasp instantly",
    "Learning & AI": "make the payoff of the skill concrete — the thing it produced, not the process of learning it",
    "AI Builds": "make one striking single-screen result that proves the whole thing was built without a developer",
}

# The voice. Varied on purpose — a feed of eight identical sentence shapes reads as a bot.
# Placeholders: {a} = "a trading bot", {Artifact} = "Trading bot", {KEYWORD} = "MONEY".
HOOK_TEMPLATES = [
    "I built {a} with nothing but Claude Code — comment {KEYWORD} to find out how",
    "I made {a} in an afternoon using only AI — comment {KEYWORD} and I'll show you the build",
    "No code, no team, no budget: {Artifact}. Comment {KEYWORD} and I'll send the build.",
    "My business needed {a}. I built it myself with AI in one sitting — comment {KEYWORD}",
    "Everyone quotes thousands for {a}. Mine cost me one afternoon and Claude Code — comment {KEYWORD}",
    "{Artifact} — built in an afternoon, no developer, no monthly fee. Comment {KEYWORD} for the walkthrough",
    "I used to pay monthly for {a}. Built my own with Claude Code instead — comment {KEYWORD}",
    "You don't need a developer for {a}. You need one afternoon and AI — comment {KEYWORD}",
    "Zero coding experience — I shipped {a} myself. Comment {KEYWORD} and I'll walk you through it.",
]

# FREE MODE. Same voice, no gate — the whole build is in the post.
#
# Why both exist: a "comment KEYWORD" post trades reach for a lead, and a giveaway post trades
# the lead for reach and trust. Early on, before there's anything to sell, trust is worth more
# and the audience that shows up is better qualified. These lines must never imply something is
# being withheld — the post *is* the delivery.
FREE_HOOK_TEMPLATES = [
    "I built {a} with nothing but Claude Code. Here's exactly how, step by step 👇",
    "{Artifact}, built by AI in one afternoon. Full build below — take it and use it.",
    "Everyone quotes thousands for {a}. I built mine for free. Here's the whole thing 👇",
    "You don't need a developer for {a}. Here's the entire build, free, no catch.",
    "I stopped paying for {a} and built my own. Steps below — copy it if it's useful.",
    "No code, no team, no budget: {Artifact}. Whole process below, nothing held back.",
    "My business needed {a}, so I made one with AI. Here's every step 👇",
    "Zero coding experience. {Artifact}, live and working. Full walkthrough below.",
    "{Artifact} in an afternoon, using only AI. Here's how — the whole build, free.",
]

# PROMO MODE. The high-reach "money influencer" shape (Bolis / Machina / Verem samples,
# 2026-07-24): lead with the OUTCOME not the process, frame it as achievable, offer the build
# FREE, and gate delivery behind a reply. This is the on-ramp toward the lead-magnet + email
# funnel the business is aiming for.
#
# HONESTY GUARD: the samples promise "$25,000/month" — we do NOT fabricate income figures.
# Our promise is a free build and not overpaying, which is true. Any concrete number belongs in
# the cover image (measured for that artifact), never invented in the hook. Same rule as the
# design bar's "no invented statistics".
PROMO_HOOK_TEMPLATES = [
    "Stop paying monthly for {a}. I'll show you how to build your own — reply {KEYWORD} and I'll send it FREE 👇",
    "You can replace {a} with 3 things: a browser, one afternoon, and AI. Reply {KEYWORD} for the free build.",
    "🚨 You don't need a developer for {a}. Reply {KEYWORD} and I'll DM you the free build.",
    "Most businesses overpay for {a}. I built mine free with AI in an afternoon — reply {KEYWORD} for the how.",
    "I built {a} that does the job of a paid tool, then gave it away. Reply {KEYWORD} for the free build 👇",
    "One afternoon is all it takes to build {a}. Yours to keep — reply {KEYWORD} for the free build.",
    "Everyone's quietly paying for {a}. You can build it yourself for free — reply {KEYWORD} and I'll show you.",
    "{Artifact} — the build your business needs, yours free. Reply {KEYWORD} 👇",
    "No code. No agency. No monthly fee. Just {a} and a free build — reply {KEYWORD}.",
]


# EDUCATIONAL MODE — LinkedIn hard rules (2026-07-27). Teach-first: the hook frames a concept or
# tool and promises an explanation, NEVER "comment KEYWORD". No engagement gate. The explanatory
# body and the AI-agent signature are enforced by prompt.py's LINKEDIN_EDU_TEMPLATE.
# Placeholders: {a} = "a booking page", {Artifact} = "Booking page". No {KEYWORD} — there is no gate.
LINKEDIN_EDU_HOOK_TEMPLATES = [
    "How a small business can actually use {a} — and where it pays off.",
    "{Artifact}: what it is, and how it changes the day-to-day for a small team.",
    "Most owners still pay a monthly fee for {a}. Here's how the AI version works, and when it's worth it.",
    "A short, practical look at {a}: what it does, who it helps, and the catch.",
    "{Artifact}, explained for people running a business — not writing code.",
    "The business case for {a}, in plain English: where it saves time and where it doesn't.",
    "What {a} really does for a small business, minus the hype.",
    "If you've heard about {a} but weren't sure it applied to you, here's the practical version.",
    "{Artifact}: the concept, the use case, and an honest read on the limits.",
]

# AD MODE — showcase (2026-07-27). The poster IS a finished ad creative; the caption shows it off
# and frames it as work by an AI agent workflow. No engagement gate. Placeholders {a}, {Artifact}.
AD_HOOK_TEMPLATES = [
    "I briefed my AI workflow to sell {a}. This is the ad it designed.",
    "A finished ad for {a} — concept, copy and art direction by my AI agent workflow.",
    "This is what AI-built ad creative looks like now: a complete ad for {a}, no design team.",
    "{Artifact} — a full ad concept my AI workflow designed start to finish.",
    "One brief, one pass: an ad for {a}, art-directed end to end by my AI agent workflow.",
    "No agency, no designer — my AI workflow built this ad for {a}.",
    "Gave my AI workflow a product and a blank page. Here's the ad for {a} it came back with.",
    "The kind of ad a small business pays thousands for — designed by my AI workflow, for {a}.",
]

# POC MODE — proof-of-concept (2026-07-28). The deliverable is a FUNCTIONAL single-file app you can
# click, not a poster; the caption shows it off and the build is saved for later reference.
POC_HOOK_TEMPLATES = [
    "I built {a} that actually works — you can click it right now.",
    "{Artifact}, as a real working prototype my AI workflow built. Try it.",
    "Not a mockup — {a} that genuinely runs, built end to end by my AI workflow.",
    "Proof it works: {a} you can use right now.",
    "One brief, one working build: {a}, as a real prototype. No dev team.",
    "From idea to {a} you can click — in a single pass.",
    "Built and running: {a}, the kind of thing AI ships in an afternoon.",
    "The concept, but real — {a} that works, not a picture of one.",
]


def _stable_index(seed, n):
    """Deterministic 0..n-1 from a string. Python's hash() is salted per process, so it would
    give a different hook on every run — which is exactly the thing we're avoiding."""
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % n


def _slugify(text, max_words=6):
    words = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()[:max_words]
    return "-".join(words) or "idea"


# "a" vs "an" goes by SOUND, not spelling, and the artifact table is full of the exceptions:
# "an one-page site" and "a AI tracker" both read as typos in a hook that has to be postable
# as-is. Prefixes that start with a vowel letter but a consonant sound, and vice versa.
CONSONANT_SOUND = ("one", "once", "eu", "ui", "uni", "use", "usa", "user", "uk")
VOWEL_SOUND = ("ai", "ap", "ll", "hour", "seo", "sop", "faq", "roi", "sms", "x")


def _article(phrase):
    first = re.split(r"[\s\-]", phrase.lower(), 1)[0]
    if first.startswith(CONSONANT_SOUND):
        return "a"
    if first.startswith(VOWEL_SOUND) or first[:1] in "aeiou":
        return "an"
    return "a"


def keywords(item):
    """Weighted keyword map for one item. Title beats body; bigrams beat single words.

    Returns {keyword: weight}. Bigrams are joined with a space so they read naturally when
    they end up as a cluster's topic label.
    """
    title = CLEAN_PAT.sub(" ", (item.get("title") or "")).lower()
    body = CLEAN_PAT.sub(" ", (item.get("body") or "")[:BODY_CHARS]).lower()

    weights = defaultdict(float)

    title_tokens = TOKEN_PAT.findall(title)
    kept = []
    for tok in title_tokens:
        tok = tok.strip(".-")
        if len(tok) < 3 or tok in STOPWORDS:
            kept.append(None)          # keep the gap so bigrams don't jump over a stopword
            continue
        weights[tok] += W_TITLE_TOKEN
        kept.append(tok)

    for i in range(len(kept) - 1):
        if kept[i] and kept[i + 1]:
            weights[f"{kept[i]} {kept[i + 1]}"] += W_TITLE_BIGRAM

    for tok in TOKEN_PAT.findall(body):
        tok = tok.strip(".-")
        if len(tok) < 3 or tok in STOPWORDS:
            continue
        weights[tok] += W_BODY_TOKEN

    return dict(weights)


def _pick(options, seed, taken):
    """Hash-pick, then linear-probe past anything already used in this feed.

    Pure hashing looked right in theory and was wrong in practice: the real corpus is lopsided
    (most posts are about learning), so a dozen clusters hit the same category and the birthday
    problem handed five of them the identical artifact. Probing keeps the choice driven by the
    idea's own content while guaranteeing a feed doesn't repeat itself until it has to.
    """
    n = len(options)
    start = _stable_index(seed, n)
    for offset in range(n):
        choice = options[(start + offset) % n]
        if choice not in taken:
            return choice
    # Every option spent. Start the rotation over rather than returning the hash pick, which
    # would land on whichever one this seed already collided with earlier.
    taken.clear()
    return options[start]


def classify(seed, group, taken=None):
    """Map a cluster to (category, artifact, cta_keyword, confident).

    Two passes. The seed keyword is what the cluster is actually ABOUT, so it gets first
    refusal; only if it matches nothing do we widen to the titles. Doing it the other way
    round mis-filed a cluster seeded on "database" as Marketing, because one of its titles
    happened to contain the word "sales". The titles are context, not the subject.
    """
    taken = taken if taken is not None else {}
    haystack = " ".join([seed] + [(i.get("title") or "") for i in group])

    for text in (seed, haystack):
        for category, pat, artifacts, cta in ARTIFACT_RULES:
            if pat.search(text):
                used = taken.setdefault(category, set())
                artifact = _pick(artifacts, seed + category, used)
                used.add(artifact)
                return category, artifact, cta, True

    category, artifacts, cta = FALLBACK
    used = taken.setdefault(category, set())
    artifact = _pick(artifacts, seed, used)
    used.add(artifact)
    return category, artifact, cta, False


def _render_hooks(slug, artifact, cta, used_hooks, used_templates):
    """Pick a gated hook (deduped by rendered line AND template shape) plus its free/promo twins.

    Shared by synthesize() and generate() so both rotate through the same voice consistently.
    """
    fields = {
        "a": f"{_article(artifact)} {artifact}",
        "Artifact": artifact[0].upper() + artifact[1:],
        "KEYWORD": cta,
    }
    start = _stable_index(slug, len(HOOK_TEMPLATES))
    order = [HOOK_TEMPLATES[(start + o) % len(HOOK_TEMPLATES)]
             for o in range(len(HOOK_TEMPLATES))]
    hook = next((t.format(**fields) for t in order
                 if t not in used_templates and t.format(**fields) not in used_hooks), None)
    if hook is None:
        hook = next((t.format(**fields) for t in order
                     if t.format(**fields) not in used_hooks), order[0].format(**fields))
    used_hooks.add(hook)
    chosen = next(t for t in order if t.format(**fields) == hook)
    used_templates.add(chosen)
    if len(used_templates) == len(HOOK_TEMPLATES):
        used_templates.clear()
    idx = HOOK_TEMPLATES.index(chosen)
    return (hook,
            FREE_HOOK_TEMPLATES[idx % len(FREE_HOOK_TEMPLATES)].format(**fields),
            PROMO_HOOK_TEMPLATES[idx % len(PROMO_HOOK_TEMPLATES)].format(**fields),
            LINKEDIN_EDU_HOOK_TEMPLATES[idx % len(LINKEDIN_EDU_HOOK_TEMPLATES)].format(**fields),
            AD_HOOK_TEMPLATES[idx % len(AD_HOOK_TEMPLATES)].format(**fields),
            POC_HOOK_TEMPLATES[idx % len(POC_HOOK_TEMPLATES)].format(**fields))


# ============================================================================
# GENERATE — the new primary path (2026-07-24 pivot).
#
# No weights, no ranking. Trawl is now an endless post generator: draw material at RANDOM from
# the scraped pool and turn it into a post of the requested TYPE. You don't pick an article;
# you say "3 LinkedIn + 2 X + 1 AI-feature" and it aggregates whatever's in the pool.
# ============================================================================

POST_TYPES = {
    # type       kind of pool material   caption voice   funnel mode
    "linkedin":  {"kind": "business",     "style": "linkedin",  "mode": "educational"},
    "twitter":   {"kind": "business",     "style": "twitter",   "mode": "promo"},
    "aifeature": {"kind": "ai-feature",   "style": "aifeature", "mode": "showcase"},
    "ad":        {"kind": "business",     "style": "ad",        "mode": "ad"},
    "poc":       {"kind": "business",     "style": "poc",       "mode": "poc"},
}

# Educational "what's new in AI" hooks. {thing} = the model/tool/paper name, {src} = where from.
# The last two are engagement-question shapes, modelled on the sample posts ("what has gotten
# better?" / a prompt-as-hook) — the image carries it, the text invites a reply.
AI_FEATURE_HOOKS = [
    "New on {src}: {thing}. Here's what it actually does — and where it's useful 👇",
    "{thing} just showed up on {src}. Plain-English breakdown of what's new 👇",
    "Everyone's about to be talking about {thing}. Here's the 30-second version.",
    "{thing} is the kind of AI release most people miss. What it changes, simply put 👇",
    "Watching the AI space so you don't have to: {thing}. What it means for a small business.",
    "{thing} dropped on {src}. Would this actually be useful to you? Here's what it does 👇",
    "This is what's new in AI this week: {thing}. What would you build with it?",
]


def _business_spec(item, taken, used_hooks, used_templates):
    kws = list(keywords(item).items())
    seed = max(kws, key=lambda kv: kv[1])[0] if kws else _slugify(item.get("title", "x"), 2)
    category, artifact, cta, confident = classify(seed, [item], taken)
    # Curated ideas carry their own concrete artifact (stashed in funnel_asset). Let it survive
    # as the post subject instead of being generalized to the nearest buildable — as long as it's
    # actually present. Category + CTA keyword still come from the classifier (sensible topic fit).
    if item.get("source") == "ideas" and (item.get("funnel_asset") or "").strip():
        artifact = item["funnel_asset"].strip()
    slug = f"{_slugify(seed, 3)}-{_slugify(artifact, 4)}-{item.get('id','')[-6:]}"
    hook, free_hook, promo_hook, edu_hook, ad_hook, poc_hook = _render_hooks(slug, artifact, cta, used_hooks, used_templates)
    return {
        "id": f"idea:{slug}",
        "hook": hook, "hook_free": free_hook, "hook_promo": promo_hook, "hook_edu": edu_hook,
        "hook_ad": ad_hook, "hook_poc": poc_hook,
        "artifact": artifact, "category": category, "keyword": cta,
        "kind": "business",
        "angle": ANGLES.get(category, ANGLES["AI Builds"]),
        "sources": [{"title": item.get("title", ""), "url": item.get("url") or "",
                     "source": item.get("source", ""), "score": int(item.get("score") or 0)}],
        "strength": 1.0,
    }


def _ai_feature_spec(item, used_hooks):
    thing = (item.get("title") or "a new AI tool").strip()
    src = {"huggingface": "Hugging Face", "github": "GitHub", "arxiv": "arXiv"}.get(
        item.get("source", ""), item.get("source", "the AI world"))
    slug = f"ai-{_slugify(thing, 5)}-{item.get('id','')[-6:]}"
    start = _stable_index(slug, len(AI_FEATURE_HOOKS))
    order = [AI_FEATURE_HOOKS[(start + o) % len(AI_FEATURE_HOOKS)] for o in range(len(AI_FEATURE_HOOKS))]
    hook = next((t.format(thing=thing, src=src) for t in order
                 if t.format(thing=thing, src=src) not in used_hooks),
                order[0].format(thing=thing, src=src))
    used_hooks.add(hook)
    return {
        "id": f"idea:{slug}",
        "hook": hook, "hook_free": hook, "hook_promo": hook,
        "artifact": thing, "category": "AI Feature", "keyword": "NEW",
        "kind": "ai-feature", "subject": thing, "source_name": src,
        "url": item.get("url") or "", "body": item.get("body") or "",
        "angle": "explain what this new AI thing does and who it helps, in plain English — educational, not salesy",
        "sources": [{"title": thing, "url": item.get("url") or "",
                     "source": item.get("source", ""), "score": int(item.get("score") or 0)}],
        "strength": 1.0,
    }


def generate(items, post_type, n=1):
    """Turn random pool items into n posts of the requested type. Endless by construction:
    random draw + dedup means repeated calls keep producing fresh posts while material lasts."""
    cfg = POST_TYPES.get(post_type, POST_TYPES["linkedin"])
    pool = list(items or [])
    random.shuffle(pool)
    out, taken, used_hooks, used_templates, seen_slugs = [], {}, set(), set(), set()
    for it in pool:
        if len(out) >= n:
            break
        try:
            spec = (_ai_feature_spec(it, used_hooks) if cfg["kind"] == "ai-feature"
                    else _business_spec(it, taken, used_hooks, used_templates))
        except Exception:
            continue
        if spec["id"] in seen_slugs:
            continue
        seen_slugs.add(spec["id"])
        spec["post_type"] = post_type
        spec["style"] = cfg["style"]
        spec["mode"] = cfg["mode"]
        # Make the top-level `hook` match the mode, so a caller reading spec["hook"] (the review
        # queue does) shows the SAME line the prompt uses — not always the gated one.
        if spec.get("kind") != "ai-feature":
            spec["hook"] = {"promo": spec["hook_promo"],
                            "free": spec["hook_free"],
                            "educational": spec["hook_edu"],
                            "ad": spec["hook_ad"],
                            "poc": spec["hook_poc"]}.get(cfg["mode"], spec["hook"])
        out.append(spec)
    return out
