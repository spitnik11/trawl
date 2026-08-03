"""Turns a ranked item into a build prompt you paste straight into Claude.

The prompt is opinionated on purpose: single file, no dependencies, screenshot-in-one-frame.
That constraint is what makes the output postable, and it matches what the book teaches.
"""

import textwrap
import time


# Prepended to every build prompt. Points the builder at the notes index over past posts so it
# works faster, keeps the voice consistent, and reuses proven POC patterns — without copying.
# The library is auto-refreshed when posts are queued (see trawl.py queue_generate).
LIBRARY_NOTE = """\
## Before you build — consult the post library
Read `Posts\\LIBRARY.md` first: the index of every post made so far — what's been covered, the
caption voice that worked, and reusable proof-of-concept components (open the referenced
`poc.html` to lift a pattern). Use it to build faster and avoid repeating an angle.
**Reference the patterns; never copy a past post.**

"""


IDEA_TEMPLATE = """\
Build **one scroll-stopping screenshot** — a single HTML file where the whole page *is* the
image. The goal is clicks and follows from AI-hesitant business owners, not a working product.
No backend, no functional app required; the picture only has to make them want it.

## The post this is for

> {hook}

- **Artifact:** {artifact}
- **Category:** {category}
- **Audience:** AI-hesitant business owners — people with a budget and a problem, not learners
{cta_line}
## The screenshot (this is the entire deliverable)

One frame that stops the scroll and reads on a phone. It must contain:

- **One dominant claim or number** — the outcome (money saved, hours back, a fee avoided). This
  is the hook of the image; make it big and make it the first thing the eye lands on.
- The artifact shown just enough to be believed — a real (plausible) mini chart, a few live-looking
  numbers, a before/after. Draw it in CSS/SVG; **never a fake/garbled chart**.
- A quiet "built free with AI / no developer / no monthly fee" tag — the offer, understated.
- It must make complete sense as a standalone image, because that is all that gets posted.

Aim it at: *{angle}*

Make the business value obvious in three seconds. The viewer should think "I want that", not
"that's clever code". A working version underneath is **optional** — skip it unless asked.

## Hard requirements

- **One file.** No build step, no npm, no external CDN — a strict CSP blocks all remote requests.
  No webfonts either; use a system font stack.
- **Cover panel screenshot-worthy in a single frame**, visible without scrolling or interacting.
- Light and dark themes both, via CSS custom properties. Responsive — it will be viewed on a phone.
- **Real, plausible content.** Real-looking business names, sensible numbers, believable data.
  No lorem. **Do not invent statistics and present them as research** — a number on the poster is
  a plausible illustration ("£2,140/mo from £734k at 3.5%"), never a cited research finding.
- Respect `prefers-reduced-motion`. Semantic HTML.

## Design bar — it must not look AI-generated

This is a marketing poster, so **one big bold headline and one large hero number are correct
here** — the "keep headings quiet / weight ≤600" guidance below is for embedded app UI, not this.
Everything else still holds: the picture must look designed, not AI-slop.

These numbers are measured from Linear, Stripe, Notion, Vercel, Superhuman, Intercom and
Raycast — where all seven agree, follow it.

**Do:**
- Establish hierarchy with size, weight and spacing *before* reaching for colour or effects
- Body 16px / weight 400 / line-height 1.5–1.6. Headings line-height 1.0–1.15 with letter-spacing
  about −2% (system fonts need less negative tracking than the proprietary faces these specs use)
- **Headings weight 600 maximum** — never 700 or 800
- At most 8 font sizes on the page: 12, 14, 16, 18, 22, 28, 40, 56
- Exactly 3 text tiers: primary ink, muted (~60%), subtle (~40%)
- **Never pure black or pure white.** Light canvas off-white, cards lift to white on top of it;
  dark canvas #0d0f12-ish, ink #171717–#292827 / #f4f4f6–#f7f8f8
- One accent colour — primary action, links and focus rings only. Never a section background.
- Spacing in multiples of 4: 4, 8, 12, 16, 24, 32, 48. Big gaps outside, tight gaps inside.
- Radius: **8px buttons and inputs, 12px cards, 16px image frames.** Pills only for tags/toggles.
- 1px hairline border ~6% off the canvas colour on cards
- Shadows: none (elevate with a lighter surface) or two stacked low offsets like
  `0 1px 2px rgba(15,15,15,.04), 0 4px 12px rgba(15,15,15,.08)` — never one large blur
- Sentence case headings. Icons one size, one stroke width, inline SVG, currentColor.
- Transitions 150ms on background/border/opacity/transform only
- Real states — empty, loading, error — not just the happy path

**Do not:**
- Purple-to-blue gradient hero, glassmorphism, glow effects, particle or animated backgrounds
- Put every element inside a rounded card; avoid huge radii on every surface
- Fake charts, meaningless statistics, invented percentages
- Emoji as section markers; centre everything; scatter pills everywhere
- Giant marketing headlines inside an application view
- Animation that delays interaction — transitions stay subtle and short

## The caption — write this too, in the chosen platform's voice

The screenshot is the image; this is the text body that ships **with** it. Deliver both.

{style_body}

**Gatekeep the how (both platforms):** show the outcome and that AI was *routed* to build it —
name NO tools, prompts, or steps. Sell judgement ("most people reach for X, that's the slow way"),
never instructions. Someone reading it must want the build, not be able to reproduce it. Never
fabricate income figures or stats.

## Where this came from

{sources}

## Post plan

- **Hook:** {hook}
{post_plan}
- **Signal strength:** {strength:.2f}
"""

STYLE_LINKEDIN = """\
**Platform: LinkedIn — stylish operator authority.**
- First line is the whole hook (it must survive the "…see more" cut).
- 3–6 short lines / small paragraphs. Confident, a little insider. Sentence case.
- One vague-but-competent line implying the workflow ("I routed a couple of AI tools at it").
- 0–2 emoji, 0–2 hashtags max. No "🚀 excited to share".
- End on one soft CTA: comment {keyword} (or "I write about building these — follow along")."""

STYLE_TWITTER = """\
**Platform: X / Twitter — punchy, funnel-first.**
- First line stops the scroll; the main tweet stays ≤280 characters.
- Same gatekeeping, fewer words. One bold emoji (e.g. 🚨) is on-brand; still ≤2.
- End in the funnel: **reply {keyword}** → DM/link → the lead-magnet email page.
- Optional 2nd tweet: one still-vague "how it works" line, then repeat the CTA."""

STYLE_AIFEATURE = """\
**AI-feature post — educational, build trust, drive follows.**
- Genuinely explain what the thing is and who it helps, in plain English. Teach, don't sell.
- No gatekeeping here — the value IS the explanation. This grows an audience that trusts you to
  filter the AI firehose for them.
- End with a light engagement nudge: "would you use this?" / "what would you build with it?" or
  "follow for the AI stuff worth knowing." No reply-keyword gate.
- Still honest: don't overstate; if it's niche, say so."""

AIFEATURE_TEMPLATE = """\
Build **one clean, shareable screenshot** — a single HTML file that IS the image — explaining a
new thing in AI to a non-expert small-business audience. Educational, calm, credible. The goal is
follows and saves, not a hard sell.

## The subject (real — do not invent capabilities)

- **What:** {subject}
- **From:** {source_name}
- **Link:** {url}
- **What it is (raw notes):** {body}

## The screenshot

A single card that teaches at a glance:
- **The name, big**, with a one-line "what it is" under it.
- **2–3 plain-English points**: what it does, who'd care, why it matters. No hype, no benchmarks
  you can't verify, no invented numbers.
- A simple visual anchor — an icon-free diagram, a before/after, or a labelled example. Draw it
  in CSS/SVG; never a fake chart.
- A quiet footer tag: the source ({source_name}) and a "what's new in AI, explained" line.
- Must read as a standalone image — that's all that gets posted.

## Hard requirements

- One file. No build step, npm, or CDN — strict CSP. System font stack only.
- Light and dark themes via CSS custom properties. Responsive; readable on a phone.
- Body text 16px+, one accent colour, 8px spacing rhythm, restrained borders, subtle shadows.
- **Accurate.** Only claim what the notes/link support. If unsure what it does, keep the claim
  general ("a new open model", "an AI coding agent") rather than inventing specifics.

## The caption — write this too

{style_body}

## Post plan
- **Hook:** {hook}
- **Angle:** {angle}
- **Type:** ai-feature (educational). No reply-keyword gate; a soft "follow / would you use this?"."""

GATED_CTA = "- **CTA keyword:** {keyword}\n"
GATED_PLAN = """\
- **CTA:** comment {keyword} → DM the link
- **Mode:** gated — the post promises the build, the DM delivers it"""

FREE_PLAN = """\
- **CTA:** none. The post *is* the delivery.
- **Mode:** free — screenshot the cover, then walk the build in the post body or a carousel.
  Nothing is held back and nothing is promised "in the DMs". This trades a lead for reach
  and trust, which is the better trade until there's something to sell."""

# Promo mode targets the high-reach "money influencer" shape (Bolis/Machina/Verem). The cover
# must sell an OUTCOME, and the funnel ends at a captured contact, not just a like.
PROMO_PLAN = """\
- **CTA:** reply {keyword} → DM the free build → (next rung) the free build links to a one-field
  email capture. That email list is the actual asset this whole mode exists to grow.
- **Mode:** promo — outcome-first, framed as achievable, given away free. Model on the sample
  posts: a big concrete outcome up front, a short "all you need is X" list, and "reply {keyword}".
- **Cover must lead with the outcome as a headline number** (money saved, hours back, fee
  avoided) — that number is the whole hook. It must be real for this artifact, never invented."""


# --------------------------------------------------------------- LinkedIn educational rules
#
# Hard rules set by the user 2026-07-27 for LinkedIn: educational-leaning, a body paragraph that
# explains how the tool/AI concept applies, NO comment/engagement hooks, and a signature marking
# the post as made by an AI agent workflow. Applied automatically whenever a LinkedIn post is
# queued (POST_TYPES["linkedin"]["mode"] == "educational"). Rebrand the signature in one place here.
AGENT_SIGNATURE = "Created by my AI agent workflow"

STYLE_LINKEDIN_EDU = """\
**Platform: LinkedIn — educational, operator-credible. HARD RULES (do not break):**
- **Educational leaning.** Teach a concept; the reader should learn something even if they never buy.
- First line is a clear, non-clickbait hook that survives the "…see more" cut. Sentence case.
- **The body must explain HOW the tool or AI business concept applies** — one short paragraph that
  makes the mechanism and the business use case concrete: what it does, when it helps, the limit.
- **No comment hooks, no engagement gates.** Never "comment {kw}", never "reply", never "DM me",
  never "follow for more". The value is the explanation itself.
- 0–1 emoji, 0–2 hashtags. No "🚀 excited to share".
- **End with this signature line, verbatim:** "{signature}"."""

EDU_PLAN = """\
- **CTA:** none. No comment / reply / DM / follow gate (hard rule). The post teaches; that's the point.
- **Mode:** educational — explain how the concept applies to a business, honestly, and sign it."""

LINKEDIN_EDU_TEMPLATE = """\
Build **one business-professional screenshot** — a single HTML file that IS the image — that
*teaches* one idea to a small-business owner. Editorial, calm, credible. The goal is that the
reader learns how {artifact} applies to a business, not that they click anything.

## The post this is for
> {hook}

- **Concept / artifact:** {artifact}
- **Category:** {category}
- **Audience:** small-business owners and operators — practical, time-poor, not coders.

## The screenshot (the entire deliverable)
A single editorial "figure" that explains at a glance:
- **A clear headline** stating the concept — not a marketing scream.
- **A short explanatory standfirst** — how it applies to a business, in one sentence.
- **One honest visual anchor** that carries the teaching: a small labelled diagram, a compact
  data table, a before/after, or a single restrained chart. Draw it in CSS/SVG. Never a fake or
  garbled chart; numbers are plausible illustrations, never invented "research".
- **A quiet signature**, bottom of the frame, verbatim: "{signature}" — the transparency mark.
- It must read as a standalone, credible image on a professional feed.

Aim it at: *{angle}*

## Visual direction — UNIQUE, editorial, semi-minimalist (deliberately NOT the generic AI look)
Break from the rounded-card / centered-hero / bright-accent "AI slop" template.
- **Typography with a point of view:** a **system serif** for display
  (`Georgia, "Times New Roman", serif`) paired with a neutral `system-ui` sans for labels/meta.
  Reads editorial/institutional, not app-generic. No webfonts (strict CSP forbids them).
- **Restraint over decoration:** near-monochrome ink on warm off-white paper, **one muted accent**
  used only for a hairline or a single figure element — not a bright blue/green, no gradient, no glow.
- **Structure via hairline rules and whitespace, not boxes.** Do not put everything in a rounded
  card. Left-aligned, generous margins, an editorial grid. A small uppercase kicker with a rule
  above the headline.
- Semi-minimalist: lots of negative space, few elements, one idea per frame.

## Hard requirements
- **One file.** No build step, npm, or CDN — strict CSP blocks remote requests. System fonts only.
- Cover reads in a single frame without scrolling. Light and dark themes via CSS custom
  properties. Responsive; readable on a phone. Respect `prefers-reduced-motion`. Semantic HTML.
- **Real, plausible content.** Real-looking names/numbers; no lorem; no invented statistics
  presented as research.

## The caption — write this too
{style_body}

## Where this came from
{sources}

## Post plan
- **Hook:** {hook}
{post_plan}
- **Signal strength:** {strength:.2f}"""


# --------------------------------------------------------------- Ad showcase (2026-07-27)
#
# The "ad" post type shows off beautiful ad creative made by the AI agent workflow. The POSTER is a
# finished advertisement; the caption reveals it and signs it. Draws on the global Ad-Copy-KB for
# proven ad patterns. No engagement gate.
AD_COPY_KB = r"C:\Users\losth\Documents\ClaudeBrain\04 Knowledge\Ad-Copy-KB"

STYLE_AD = """\
**Platform caption: the showcase voice — "my AI workflow designed this ad."**
- First line frames the reveal: this is a complete ad concept the AI agent workflow produced.
- 2–4 short lines. Confident, a little proud, not hypey. Point at ONE thing that makes the ad work
  (the hook, the offer, the art direction) so it reads as craft, not luck.
- A line on *why* it works is welcome — it teaches and builds authority.
- No fabricated performance ("this ad made $X"). Show the craft, never invented results.
- 0–1 emoji, 0–2 hashtags. **End with the signature line, verbatim:** "{signature}"."""

AD_PLAN = """\
- **CTA:** none required — this showcases the ad creative itself. A soft "I design these with AI —
  follow along" is fine; no comment/reply/DM gate.
- **Mode:** ad — the poster IS a finished ad concept; the caption shows it off and signs it."""

AD_TEMPLATE = """\
Build **one beautiful advertisement** — a single HTML file that IS the ad creative — for
{artifact}. This post exists to SHOW OFF ad creative made by an AI workflow, so the ad has to look
like something a good agency would ship. This is the portfolio piece; make it genuinely impressive.

## The product this ad sells
- **Offer / artifact:** {artifact}
- **Category:** {category}
- Invent a plausible brand — a name and a one-line value prop — so the ad reads as a real brand's.

## The ad creative (the entire deliverable)
A single, striking ad frame that could run as a real paid ad and stop a scroll:
- **A dominant headline** — the promise/benefit, the first thing the eye lands on.
- **A hero visual built in CSS/SVG** — a clean product mock, a bold benefit illustration, or a
  confident type treatment. Never a fake/garbled chart; plausible content only.
- **The offer**, stated clearly, and **one primary CTA button** ("Get a free quote", "Start free").
  One CTA, unmistakable.
- **A simple invented brand mark / logotype**, so it reads as a real brand's ad.
- **A discreet credit**, small and out of the way: "Ad concept · designed by my AI agent workflow".
- Works as a standalone image — that's what gets posted.

## Draw on the ad-research library (this is what it's for)
Before designing, skim `{kb}` for proven patterns: strong openers (`Hooks/`), headline formulas
(`Headlines/`), offers and CTAs (`Advertisements/`, `Landing-Pages/`), and visual patterns
(`UI-Inspiration/`). Use the *patterns*; invent the specifics. If a teardown there fits this
product, lift the structure, never the words. (If the folders are still empty, use best-practice
direct-response ad structure.)

## Visual direction — polished, modern, brand-grade (NOT AI-slop)
- Confident hierarchy: one big headline, one hero, one CTA. High intent, low clutter.
- One strong brand colour used deliberately (the CTA + one accent) over a clean neutral system.
- Real type scale, generous spacing, a crafted CTA button. Light and dark both.
- No purple→blue gradient hero, no glassmorphism, no glow, no fake charts, no emoji-as-decoration.
- It should look art-directed — like a brand chose every element on purpose.

## Hard requirements
- One file. No build step, npm, or CDN — strict CSP. System fonts only (no webfonts).
- Reads in one frame without scrolling. Light + dark via CSS custom properties. Responsive;
  phone-legible. Respect `prefers-reduced-motion`. Semantic HTML.
- Real, plausible content — no lorem, no invented statistics presented as research.

## The caption — write this too
{style_body}

## Where this came from
{sources}

## Post plan
- **Hook:** {hook}
{post_plan}
- **Signal strength:** {strength:.2f}"""


# --------------------------------------------------------------- Proof of concept (2026-07-28)
#
# The "poc" type ships a FUNCTIONING single-file app (not a poster), drawing on the UI reference
# library, saved to a vault POC library for later reuse.
UI_LIBRARY_DIR = r"C:\Users\losth\Documents\ClaudeBrain\04 Knowledge\UI-Library"
POC_LIBRARY = r"C:\Users\losth\Documents\ClaudeBrain\04 Knowledge\POC-Library"

STYLE_POC = """\
**Platform caption: the builder-showcase voice — "a working thing my AI workflow built."**
- Lead with the reveal: this is a REAL, working proof of concept you can click, not a mockup.
- 2–4 short lines. Point at ONE thing it does that proves it works.
- A line on the value / who'd use it. No fabricated performance numbers.
- 0–1 emoji, 0–2 hashtags. **End with the signature line, verbatim:** "{signature}"."""

POC_PLAN = """\
- **CTA:** none required — the working demo is the point. A soft "I build these with AI — follow
  along" is fine; no comment/reply gate.
- **Mode:** poc — deliverable is a FUNCTIONING single-file app, saved to the vault POC library."""

POC_TEMPLATE = """\
Build a **working proof of concept** — a single self-contained HTML file that ACTUALLY FUNCTIONS in
the browser — for {artifact}. This is not a poster or a mockup: it must really work when opened, so
a viewer can click, type, and see it respond. Impress with craft AND with the fact that it runs.

## What to build
- **Artifact / concept:** {artifact}
- **Category:** {category}
- A genuinely usable proof of concept of that concept — the core interaction, working. Invent a
  plausible brand/name. Real, believable content and behaviour; no lorem, no dead buttons.

## It must actually run
- **One file.** Inline CSS + vanilla JS. No build step, no npm, no external CDN or webfonts
  (a strict CSP blocks remote requests). Everything self-contained.
- **Real interactivity:** inputs update state, buttons do something, results compute/render live.
  Persist with in-memory state or `localStorage`. Handle empty / loading / error states.
- Works standalone when the file is opened, and renders fine inside an iframe.
- Light and dark themes via CSS custom properties. Responsive; keyboard-usable; respect
  `prefers-reduced-motion`. Semantic HTML.

## Make it look designed (use the UI reference library)
Skim `{ui_lib}` (or run its `scripts/search.py`) for a clean, professional pattern that fits —
dashboards, tools, forms. Borrow the PRINCIPLES — clear type hierarchy, generous whitespace, one
restrained accent, real components — never a literal copy. It should read like a real product's MVP.

## Save it for later reference (do this as you build)
- Keep `poc.html` in the post folder (the review board renders it).
- Also copy the finished POC into the vault POC library at `{poc_lib}\<slug>\poc.html` with a short
  `README.md` (what it is, the concept, the date) so it can be reused. Worth-sharing ones can be
  published as a standalone Artifact.

## The caption — write this too
{style_body}

## Where this came from
{sources}

## Post plan
- **Hook:** {hook}
{post_plan}
- **Signal strength:** {strength:.2f}"""


def _direction(note):
    """An 'operator direction' block injected into any build prompt when the Generate form's note
    is set. It steers subject/angle/tone but must never override the post-type rules."""
    note = (note or "").strip()
    if not note:
        return ""
    return ("## Creative direction (from the operator) — follow this\n"
            f"{note}\n\n"
            "Shape the subject, angle, and tone to this direction. **Do not break the post-type rules "
            "below** (e.g. LinkedIn stays educational with no engagement gate and keeps the signature; "
            "the honesty guard and single-file poster always hold). If the direction conflicts with a "
            "rule, follow the rule and get as close to the direction as it allows.\n\n")


def build_idea(it, mode="gated", style=None, note=""):
    """Build prompt for a synthesised idea (a cluster of posts).

    mode: "gated" (comment→DM), "free" (post delivers everything), "promo" (outcome-first
    lead-magnet, Bolis-style, on-ramp to email capture).
    style: "linkedin" | "twitter" — the voice of the accompanying caption. Defaults from mode
    (promo→twitter, else linkedin) so callers that only pass mode still get a sensible caption.
    """
    srcs = it.get("sources") or []
    if srcs:
        lines = "\n".join(
            f"- {s.get('title', '')} — {s.get('source', '?')}"
            + (f" ({s.get('score')} pts)" if s.get("score") else "")
            + (f"\n  {s['url']}" if s.get("url") else "")
            for s in srcs[:4]
        )
    else:
        lines = "- (manual entry)"

    kw = it.get("keyword", "BUILD")
    hook = {
        "free": it.get("hook_free") or it.get("hook", ""),
        "promo": it.get("hook_promo") or it.get("hook", ""),
        "educational": it.get("hook_edu") or it.get("hook", ""),
        "ad": it.get("hook_ad") or it.get("hook", ""),
        "poc": it.get("hook_poc") or it.get("hook", ""),
    }.get(mode, it.get("hook", ""))

    # PoC path: the deliverable is a FUNCTIONING single-file app (saved to the vault POC library).
    if mode == "poc":
        return LIBRARY_NOTE + _direction(note) + POC_TEMPLATE.format(
            style_body=STYLE_POC.format(signature=AGENT_SIGNATURE),
            hook=hook, artifact=it.get("artifact", ""), category=it.get("category", ""),
            ui_lib=UI_LIBRARY_DIR, poc_lib=POC_LIBRARY,
            sources=lines, post_plan=POC_PLAN, strength=float(it.get("strength", 0) or 0),
        )

    # Ad showcase path: the poster is a finished ad creative; caption shows it off + signs it.
    if mode == "ad":
        return LIBRARY_NOTE + _direction(note) + AD_TEMPLATE.format(
            style_body=STYLE_AD.format(signature=AGENT_SIGNATURE),
            hook=hook,
            artifact=it.get("artifact", ""),
            category=it.get("category", ""),
            kb=AD_COPY_KB,
            sources=lines,
            post_plan=AD_PLAN,
            strength=float(it.get("strength", 0) or 0),
        )

    # LinkedIn educational path: its own template (teach-first, no gate, signed, editorial design).
    if mode == "educational":
        return LIBRARY_NOTE + _direction(note) + LINKEDIN_EDU_TEMPLATE.format(
            style_body=STYLE_LINKEDIN_EDU.format(kw=kw, signature=AGENT_SIGNATURE),
            hook=hook,
            artifact=it.get("artifact", ""),
            category=it.get("category", ""),
            angle=it.get("angle", "Teach how this applies to a business, concretely and honestly."),
            sources=lines,
            post_plan=EDU_PLAN,
            strength=float(it.get("strength", 0) or 0),
            signature=AGENT_SIGNATURE,
        )

    plan = {
        "free": FREE_PLAN,
        "promo": PROMO_PLAN.format(keyword=kw),
    }.get(mode, GATED_PLAN.format(keyword=kw))

    style = style or ("twitter" if mode == "promo" else "linkedin")
    style_body = (STYLE_TWITTER if style == "twitter" else STYLE_LINKEDIN).format(keyword=kw)

    return LIBRARY_NOTE + _direction(note) + IDEA_TEMPLATE.format(
        style_body=style_body,
        hook=hook,
        artifact=it.get("artifact", ""),
        category=it.get("category", ""),
        cta_line="" if mode == "free" else GATED_CTA.format(keyword=kw),
        angle=it.get("angle", "Show the artifact working, end to end."),
        sources=lines,
        post_plan=plan,
        strength=float(it.get("strength", 0) or 0),
    )


def build_ai_feature(it, note=""):
    """Build prompt for an 'AI-feature' educational post — subject is a real model/tool/paper."""
    return LIBRARY_NOTE + _direction(note) + AIFEATURE_TEMPLATE.format(
        subject=it.get("subject") or it.get("artifact", ""),
        source_name=it.get("source_name", "the AI world"),
        url=(it.get("url") or (it.get("sources") or [{}])[0].get("url", "")) or "(link n/a)",
        body=(it.get("body") or "(no description scraped — keep claims general)")[:500],
        hook=it.get("hook", ""),
        angle=it.get("angle", ""),
        style_body=STYLE_AIFEATURE,
    )
