"""Scoring and theming.

Design note: comments are weighted ~1.5x score. Upvotes mean agreement; comments mean
unresolved argument or unanswered pain. Pain is what content is for.

Rewritten 2026-07-23 after an audit measured a 66% junk rate in the top 50 (80% in the top 10).
Every change below is a response to something demonstrated in the data, not a guess. See the
notes on each constant.

All tuning constants live here at the top on purpose.
"""

import math
import re
import time

# --- weights (must sum to 1.0 for the base score) ---
W_ENGAGEMENT = 0.45
W_VELOCITY = 0.25
W_RECENCY = 0.30

COMMENT_WEIGHT = 1.5      # comments vs score inside engagement
RECENCY_HALFLIFE_H = 168  # ~1 week
VELOCITY_FLOOR_H = 2.0    # don't divide by ~0 for brand-new posts
MAX_AGE_DAYS = 365        # hard cutoff — stale pain is not current pain
# One number instead of two thresholds. The old `score < 3 and comments < 1` pair almost
# never bound, because any item with a single comment passed.
#
# Lowered 6 -> 3 for the idea-synthesis pivot. Ideas are now built from CLUSTERS of posts, so a
# single low-vote post is still useful raw material — "Show HN: I built a trading bot using
# alpaca.markets" scores 1 point but is exactly the right shape. Topical fit now matters more
# than engagement; the junk, off-topic and inert gates still do the filtering.
MIN_ENGAGEMENT_RAW = 3    # score + 2*comments

# Topical relevance. A well-formed question about something else is still useless:
# "Ask HN: Instagram blocked my new account what can I do?" ranked #1 without this.
# Includes artifact nouns — "I made a dashboard for my running data" is on-audience even
# though it names no language or tool.
RELEVANT_PAT = re.compile(
    r"\b(cod(e|ing)|program\w*|develop(er|ment)?|software|engineer\w*|"
    r"app|apps|website|web\s?dev\w*|front-?end|back-?end|full-?stack|"
    r"ai|llm|claude|chatgpt|gpt|copilot|cursor|gemini|agentic|"
    r"python|javascript|typescript|react|html|css|sql|kotlin|swift|"
    r"build\w*|script\w*|debug\w*|deploy\w*|github|ide|terminal|vibe.?cod\w*|"
    r"dashboard|tracker|calculator|generator|visuali[sz]\w*|game|site|tool|"
    # things beginners actually get stuck on — each of these was a real dropped post
    r"docker|npm|node|install\w*|command line|error|package|library|framework|"
    r"rate limit|free tier|api|token|repo|repositor\w+|localhost|server|database|"
    # business-audience vocabulary — they describe the same problems without dev words
    r"automat\w*|workflow|spreadsheet|excel|crm|invoic\w*|booking|scheduling|"
    r"no.?code|low.?code|zapier|airtable|notion|make\.com|chatbot|"
    r"client|customer|lead|onboard\w*|template|integrat\w*|saas|software|"
    # finance & trading — the business pivot targets this and it was entirely missing, so every
    # trading post failed the relevance gate before the junk penalty even got a chance
    r"trad(e|ing|er)|stock|equit\w+|market|portfolio|invest\w*|backtest\w*|broker|ticker|"
    r"crypto|finance|financial|revenue|profit|pricing|payroll|accounting|budget)\b",
    re.I,
)

BONUS_QUESTION = 1.30
BONUS_BUILDABLE = 1.40
PENALTY_SEEN = 0.30       # already surfaced in an earlier run
PENALTY_JUNK = 0.25       # a penalty, NOT a drop — see the note on JUNK_PATTERNS

# Strong question signals. Deliberately \b-anchored rather than ^-anchored: in a Reddit
# comment the question is usually mid-paragraph, not at the start of the string.
QUESTION_STRONG = re.compile(
    r"(^\s*ask hn\b|"
    r"\bhow (can|do|would|should) (i|you|we)\b|"
    r"\bwhat (do|would|are) you (use|recommend|do)\b|"
    r"\bwhat'?s the best (way|tool|approach)\b|"
    r"\bis there a (way|tool|better)\b|"
    r"\bhas anyone\b|\bcan someone\b|\banyone (know|else)\b|"
    r"\bwhat should i\b|\bwhere do i\b|\bhow the hell do\b|"
    r"\bwhy (do|does|is|am|can'?t) (i|my|it)\b)",
    re.I,
)

# Weak signals — TITLE ONLY. Measured: matching these in the body produced garbage —
# "?" from a rhetorical line in a launch post, "stuck" from "my ELO stuck around 1600",
# "new to" from "if you're new to werewolf romance". All bought a free x1.3.
QUESTION_WEAK = re.compile(
    r"(\?|\bstruggl\w*|\bstuck\b|\bconfus\w*|\bbeginner\b|\bnewbie\b|\bnew to\b|"
    r"\bfirst time\b|\bcan'?t get\b|\bdoesn'?t work\b|\bnot working\b)",
    re.I,
)

# Measured: the old pattern fired on 92% of the corpus, so the x1.4 was a constant, not a
# signal. `ui` alone matched 119 times and NEVER as the word "UI" — always inside built /
# building / quickly. Bare `built|build|made` also let narrative past tense qualify, which is
# exactly how a 15-year anniversary essay took rank #1.
# Now: word-bounded, and either subject-anchored (someone built a thing) or an artifact noun
# that describes a screenshot-worthy single-frame output — which is what prompt.py needs.
BUILDABLE_PAT = re.compile(
    r"\b(?:"
    # someone built something
    r"(?:i|we|they|she|he|friends|someone|anyone|people|students)\s+"
    r"(?:just\s+|all\s+)?(?:built|made|created|shipped)\b"
    # built/building <a|my|their> [first] <artifact>
    r"|(?:built|building|made|making|created|shipped)\s+"
    r"(?:an?\s+|my\s+|their\s+|the\s+)?(?:first\s+)?(?:\w+\s+)?"
    r"(?:apps?|sites?|websites?|games?|tools?|projects?|dashboards?|bots?|programs?)\b"
    # my first [working] app
    r"|my\s+first\s+(?:\w+\s+)?(?:app|site|website|game|tool|project|program)\b"
    r"|(?:want|wanted|trying|learning|how)\s+to\s+build\b"
    r"|how\s+(?:i|we|to)\s+(?:built|build|make)\b"
    r"|(?:project|app|tool|site|game|dashboard)\s+idea\b"
    # artifact nouns that describe a screenshot-worthy single-frame output
    r"|(?:dashboard|landing\s*page|portfolio\s*site|data\s*viz|visuali[sz]ation|"
    r"animation|infographic|chart|clone\s+of|single-?page|generator|calculator|tracker)\b"
    r")",
    re.I,
)

# Industry drama. Deliberately does NOT include rate-limit, deprecated, source-code or banned:
# those are things beginners genuinely hit and ask about ("I keep hitting the rate limit on the
# free tier, what do I do?" is a perfect post, and the old pattern deleted it).
NOISE_PAT = re.compile(
    r"\b(leak(ed)?|lawsuit|sue[sd]?|acquisition|acquir(ed|es|ing)|funding round|"
    r"layoff|ipo|valuation|earnings|"
    r"steganograph\w*|telemetry|obfuscat\w*|reverse.?engineer\w*|decompil\w*|"
    r"shutting down|discontinu\w*|outage|dumbed down|nerfed|"
    r"benchmark|outperform\w*)\b",
    re.I,
)

# Categories measured in real data — classes of post with high engagement and no value to an
# audience that has never written code.
#
# TWO RULES, both learned by getting them wrong:
#   1. Matched against the TITLE ONLY. Matching body[:600] meant one incidental word anywhere
#      in a long post killed it.
#   2. Applied as a PENALTY, not a drop. Dropping gave no recovery from a single false hit,
#      and it deleted 12 of 16 known-good titles while the counters read like success.
# Tokens naming things beginners legitimately build or break on (recipe, invoice, waitlist,
# docker, sdk) were removed for the same reason.
JUNK_PATTERNS = [
    # "lessons learned" / "my journey" / "2 years of" are the standard phrasing of the
    # beginner-success post this tool exists to find. Only true meta-gratitude stays.
    ("gratitude", re.compile(
        r"(\bthanks?\s+(hn|to\s+everyone|for\s+\d+)|\banniversar\w*|\bretrospectiv\w*|"
        r"\blife'?s work\b)", re.I)),
    ("funding", re.compile(
        r"(^\s*launch\s+hn\b|\(\s*yc\s+[swfxp]?\d{2}|\bseries\s+[a-e]\b|\brais(ed|ing)\s+\$|"
        r"\bwe'?re\s+hiring\b|\bwho'?s\s+hiring\b)", re.I)),
    # Career/philosophy debate — 5 of the 10 junk items in the re-audit had no category.
    ("career", re.compile(
        r"\b(career|job market|hiring market|still (a good|worth it)|"
        r"grow as an? (software )?engineer|human intelligence|commodit\w+|dark factory|"
        r"replace (developers|programmers|engineers))\b", re.I)),
    ("infra", re.compile(
        r"\b(self-?hosted|on-?prem|kubernetes|k8s|api gateway|reverse proxy|"
        r"oauth2?|middleware|orchestrat\w+|kafka|gpu capacity|daemon)\b", re.I)),
    ("langdev", re.compile(
        r"\b(programming language|compiler|transpil\w*|dsl|type system|bytecode|lexer|"
        r"interpreter|parser generator)\b", re.I)),
    ("insider", re.compile(
        r"(^\s*tell\s+hn\b|\b(vim|emacs|neovim|technical debt|monorepo|microservices|"
        r"clean code|craftsmanship|the death of|is dead|hype cycle)\b)", re.I)),
    # Narrowed for the business pivot. "trading bot", "algorithmic trading", "sec filing" and
    # "payroll" are now TARGET content — a money audience wants exactly those builds. Only the
    # hype markers stay, because that crowd doesn't buy, it speculates.
    ("crypto-hype", re.compile(
        r"\b(web3|defi|tokenomics|nft|memecoin|shitcoin|moon(ing|shot)|pump and dump)\b", re.I)),
    ("security", re.compile(
        r"\b(pentest\w*|penetration test|cve-\d|vulnerab\w+|post-?quantum|"
        r"zero-?knowledge|threat model)\b", re.I)),
    # Also narrowed: "SEO" and "ads" are legitimate business builds now (an SEO audit tool, an
    # ad-copy generator). Only growth-hack hype remains.
    ("growth-hype", re.compile(
        r"\b(growth hack\w*|going viral|get rich|passive income scheme|affiliate army)\b", re.I)),
    ("unrelated", re.compile(
        r"\b(trope|romance|fanfic|horoscope|astrolog\w+|betting odds|casino)\b", re.I)),
]

# theme -> (pattern, which funnel asset it feeds)
# Order matters: the first match wins, so the themes that predict quality come first.
# `showcase` no longer matches the literal "Show HN:" prefix — measured, that made it the
# junk bucket (30 of 89 items, 68% junk) because 35 of the top 50 carry that prefix.
# Every alternative is word-bounded: `ship` was matching inside craftsmanship, `free`
# inside freelancers, `live` inside livelihood, `bug` inside a changelog line.
THEMES = [
    ("no-code-beginner", re.compile(r"\b(no cod\w*|zero cod\w*|never coded|non.?technical|"
                                    r"not a (dev|programmer)|know (0|nothing)|no experience|"
                                    r"beginner|newbie|vibe.?cod\w*|first app|complete novice)\b",
                                    re.I),
     "Project 1 + Guide 5"),
    ("learning",         re.compile(r"\b(learn\w*|tutorial|course|how do i start|"
                                    r"getting started|roadmap|teach)\b", re.I),
     "Guide 1: The 20-Minute Rule"),
    ("what-to-build",    re.compile(r"\b(what should i (build|make)|project idea|ideas for|"
                                    r"beginner project)\b", re.I),
     "Bonus: What To Build Next"),
    ("it-broke",         re.compile(r"\b(error|broke|broken|bug|bugs|fail\w*|crash\w*|stuck|"
                                    r"debug\w*)\b|doesn'?t work|not working", re.I),
     "When It Breaks + Guide 4"),
    ("mobile",           re.compile(r"\b(phone|mobile|android|ios|expo|react native|apk)\b", re.I),
     "Project 6"),
    ("shipping",         re.compile(r"\b(deploy\w*|ship|shipped|shipping|publish\w*|host\w*|"
                                    r"live|production|app store|play store|domain)\b", re.I),
     "Project 7"),
    ("cost-tools",       re.compile(r"\b(free|cost|pricing|cheap|budget|token cost|"
                                    r"expensive|subscription)\b", re.I),
     "Guide 3: The Free Stack"),
    ("showcase",         re.compile(r"\b(i (built|made|created)|we (built|made)|my first)\b", re.I),
     "Proof / authority content"),
]


def _pct(values):
    """Percentile rank, 0..1.

    Replaces min-max. Measured: one 832-point post set the max for both engagement and
    velocity, pinning itself at 1.0 and squashing the other 88 items underneath. Percentile
    rank is immune to that — a single outlier occupies one slot, not the whole ceiling.
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [0.5]
    order = sorted(range(n), key=lambda i: values[i])
    out = [0.0] * n
    for pos, i in enumerate(order):
        out[i] = pos / (n - 1)
    return out


def junk_reason(item):
    """Return the junk category name, or None. TITLE ONLY — see the note on JUNK_PATTERNS."""
    title = item.get("title", "")
    for name, pat in JUNK_PATTERNS:
        if pat.search(title):
            return name
    if NOISE_PAT.search(title):
        return "industry-news"
    return None


def theme_of(item):
    """Title first, across all themes. Only fall back to the body if the title says nothing.

    The old version concatenated the title twice and searched title+body together, with a
    comment claiming that weighted the title. It didn't — `search()` is boolean, so theme was
    decided purely by THEMES order, and a body match beat a title match whenever it sat
    earlier in the list. 14 of the top 50 were mis-themed as a result.
    """
    title = item.get("title", "")
    for name, pat, asset in THEMES:
        if pat.search(title):
            return name, asset
    body = item.get("body", "")[:300]
    for name, pat, asset in THEMES:
        if pat.search(body):
            return name, asset
    return "general", "Top-of-funnel"


def score_all(items, known_ids=frozenset(), verbose=True):
    """Score, filter, and sort. Items with no actionable content are dropped, not ranked."""
    if not items:
        return []

    now = time.time()
    cutoff = now - MAX_AGE_DAYS * 86400
    dropped = {"old": 0, "thin": 0, "offtopic": 0, "inert": 0, "junk_penalised": 0}

    kept = []
    for it in items:
        if (it.get("created_utc") or now) < cutoff:
            dropped["old"] += 1
            continue
        if int(it.get("score") or 0) + 2 * int(it.get("comments") or 0) < MIN_ENGAGEMENT_RAW:
            dropped["thin"] += 1
            continue

        title = it.get("title", "")
        body = it.get("body", "")[:400]

        # TITLE ONLY. Matching the body let "Ask HN: Instagram blocked my new account"
        # through at #2, because its body happened to mention an app. Body text can only
        # ever inflate — the same flaw the first audit found in the bonus multipliers.
        if not RELEVANT_PAT.search(title):
            dropped["offtopic"] += 1
            continue

        is_q = bool(QUESTION_STRONG.search(title) or QUESTION_STRONG.search(body)
                    or QUESTION_WEAK.search(title))
        is_b = bool(BUILDABLE_PAT.search(title) or BUILDABLE_PAT.search(body))

        # The actionability gate. A post that is neither a question nor a concrete build
        # has nothing for the prompt generator to work with — emitting a prompt for it
        # produces the generic fallback angle and a hook that means nothing.
        if not is_q and not is_b:
            dropped["inert"] += 1
            continue

        it["_is_q"], it["_is_b"] = is_q, is_b
        it["_junk"] = junk_reason(it)   # penalised later, never dropped
        if it["_junk"]:
            dropped["junk_penalised"] += 1
        kept.append(it)

    if verbose and any(dropped.values()):
        print("  filtered: " + ", ".join(f"{v} {k}" for k, v in dropped.items() if v))

    if not kept:
        return []

    eng, vel, rec = [], [], []
    for it in kept:
        age_h = max((now - (it.get("created_utc") or now)) / 3600.0, 0.01)
        s = max(int(it.get("score") or 0), 0)
        c = max(int(it.get("comments") or 0), 0)
        eng.append(math.log1p(s) + COMMENT_WEIGHT * math.log1p(c))
        vel.append(s / max(age_h, VELOCITY_FLOOR_H))
        rec.append(math.exp(-age_h / RECENCY_HALFLIFE_H))

    p_eng, p_vel = _pct(eng), _pct(vel)

    for i, it in enumerate(kept):
        base = W_ENGAGEMENT * p_eng[i] + W_VELOCITY * p_vel[i] + W_RECENCY * rec[i]

        mult = 1.0
        if it["_is_q"]:
            mult *= BONUS_QUESTION
        if it["_is_b"]:
            mult *= BONUS_BUILDABLE
        if it["_junk"]:
            mult *= PENALTY_JUNK
        if it["id"] in known_ids:
            mult *= PENALTY_SEEN

        theme, asset = theme_of(it)
        it["rank_score"] = round(base * mult, 5)
        it["theme"] = theme
        it["audience"] = it.get("audience") or "builder"
        it["funnel_asset"] = ("Proof content — shows what AI can build"
                              if it["audience"] == "business" else asset)
        it["_why"] = {
            "engagement": round(p_eng[i], 3),
            "velocity": round(p_vel[i], 3),
            "recency": round(rec[i], 3),
            "question": it["_is_q"],
            "buildable": it["_is_b"],
            "junk": it["_junk"] or "",
            "seen_before": it["id"] in known_ids,
        }

    kept.sort(key=lambda x: x["rank_score"], reverse=True)
    return kept
