# Output Design Rules

Distilled from the DESIGN.md specs for **Linear, Stripe, Notion, Vercel, Superhuman, Intercom, Raycast**
(`Z:\Claude app\_reference\awesome-design-md\design-md\`). Written to be pasted into a build prompt for
single-file HTML artifacts.

---

## 1. What the seven have in common

**Border radius — near-identical scale in all seven.**
4 / 6 / 8 / 12 / 16 px, plus 9999px for pills only. Linear, Stripe, Notion, Intercom, Superhuman and
Raycast list literally the same first five steps (Raycast uses 10px instead of 12px for `lg`). Nothing
sits above 24px except true pills. Raycast states the rule explicitly: "clusters tightly between 4 and
16px, with most chrome at 6–10px."
Assignment is also shared: **buttons 8px, cards 12px, product-screenshot frames 16px.** Only Stripe and
Vercel pill their marketing CTAs; Linear, Notion, Intercom and Superhuman each explicitly say *don't*
pill buttons.

**Spacing — 4px or 8px base, 96px section rhythm.**
Base unit 4px (Linear, Vercel, Notion) or 8px (Stripe, Intercom, Superhuman, Raycast). Section gap is
96px in Linear, Intercom, Raycast and Notion; 64–96px in Stripe and Superhuman; Vercel runs 64–96px with
hero bands to 192px. Card interior padding is 24px standard, 32px for testimonial/pricing, 48px for CTA
banners (Linear, Intercom identical). Raycast caps it: "Don't pad cards with 32px+ … runs tight at 16–24px."
Container max-width 1200–1280px (Linear 1280, Stripe ~1200, Notion 1280, Raycast ~1240), Superhuman
narrower at 960–1100, Vercel widest at 1400.

**Type scale — 7–9 discrete tiers, no 700 weight on display.**
Sizes cluster on 12 / 13 / 14 / 16 / 18 / 20 / 22 / 28 / 40 / 56 / 72–80.
Display weight ceilings: Linear 600, Notion 600, Vercel 600, Raycast 600, Intercom 500, Superhuman
460–540, Stripe **300**. Vercel: "Don't promote the geometric sans to weight 700." Body is 400
everywhere; buttons are 500 at 14–16px.

**Line-height — inverted ladder.**
Display tightens, body relaxes. Display 0.96–1.15 (Superhuman 0.96 @64px, Linear/Notion 1.05 @80px,
Intercom 1.05 @72px); body 1.5–1.6 (Linear 1.50, Intercom 1.50, Notion 1.55, Raycast 1.60, Vercel
24/16 = 1.5). Intercom names the principle: "tighten on display, relax on body."

**Negative letter-spacing on display, ~2.5–4% of font size.**
Linear −3px @80px (3.75%), Vercel −2.4px @48px, Superhuman −1.32px @48px, Intercom −2.0px @72px
(≈3%), Notion −2px @80px, Stripe −1.4px @56px. It scales proportionally down to 0 at body size.
Raycast is the sole exception — slightly *positive* tracking (0.1–0.4px) because it is dark-canvas UI chrome.

**One accent colour, used only on action.**
Linear: lavender #5e6ad2 "ONLY for brand mark, primary CTA, focus ring, link emphasis." Intercom: Fin
Orange #ff5600 only on Fin AI CTAs, never as a background. Raycast: white is the *only* CTA colour;
saturated accents are banned from chrome and confined to illustrations. Vercel: near-black #171717 is
the CTA — "Black ink IS the conversion target." Stripe: one filled indigo button per band. Six of the
seven have an explicit "don't introduce a second accent" rule. Notion is the deliberate exception
(purple CTA + pastel card tints) and still bans purple for body text or large surfaces.

**Ink is never pure black; dark canvas is never pure black.**
Superhuman #292827 ("never pure black — the warm grey is part of the brand"), Vercel #171717 with
#4d4d4d body, Notion #1a1a1a / #37352f, Intercom #111111, Stripe navy #0d253d.
Dark canvases: Linear #010102 ("Don't use #000000 true black"), Raycast #07080a.
Light canvases are usually off-white: Intercom cream #f5f1ec, Notion #f6f5f4, Vercel #fafafa,
Superhuman #fafaf8.

**Three or four text tiers, not two.** Linear ink / ink-muted / ink-subtle / ink-tertiary; Intercom the
same four; Raycast ink / body / mute / ash / stone; Vercel ink / body / mute.

**Hairlines carry structure; shadows are optional.**
Hairline borders sit ~5–8% off the canvas: #ebebeb (Vercel), #e3e8ee (Stripe), #e5e3df (Notion),
#e8e4dd (Superhuman), #d3cec6 (Intercom), #23252a (Linear), #242728 (Raycast).
**Three of the seven use no drop shadows at all** — Linear, Intercom and Raycast build elevation purely
from a surface-colour ladder plus a 1px hairline. Where shadows exist they are low-alpha and often
tinted: Stripe `rgba(0,55,112,0.08) 0 1px 3px`; Notion `rgba(15,15,15,0.04) 0 1px 2px` →
`rgba(15,15,15,0.08) 0 4px 12px`; Superhuman `0 1px 3px rgba(0,0,0,0.08)`. Vercel *stacks* several tiny
offsets plus an inset ring — `0 1px 1px #00000005, 0 2px 2px #0000000a` — and says: "never a single
8-px-blur generic drop."

**Real product UI is the decoration.** Linear, Stripe, Intercom, Notion, Vercel and Raycast all make
product screenshots/mockups the load-bearing visual of every section. Linear and Intercom both add:
"No atmospheric gradients, no spotlight cards." Decorative gradient, where used, is a single
hero-scale event — Vercel ("hero scale only, never miniaturised"), Raycast ("exactly once per page,
never repeat deeper in the page").

**Motion** is not captured in any of the seven specs; the collection's own note recommends 150–200ms ease.

---

## 2. Professional vs generic

| Dimension | The seven do | Generic AI output does |
|---|---|---|
| Colour | 1 accent on actions only; 3–4 neutral ink tiers; off-white or near-black canvas | 3–5 competing accents; pure #000 on pure #fff; accent used as background fill |
| Type | 7–9 fixed tiers; display 500–600 max; body 400/16px | Arbitrary sizes; `font-weight: bold` everywhere; 14px body |
| Tracking | −2.5 to −4% on display, 0 at body | Default tracking at every size, or letter-spaced all-caps headings |
| Line-height | 1.0–1.15 display, 1.5–1.6 body | 1.5 on everything, so headlines look loose and cheap |
| Spacing | 4/8px grid; 96px between sections; 24–32px inside cards | Random 15px/25px/50px values; equal padding everywhere |
| Borders | 1px hairline ~5–8% off canvas, on every card | No borders (relies on shadow), or 2px+ visible strokes |
| Radius | 8px buttons / 12px cards / 16px frames, consistently | 16–24px on everything, or `rounded-full` on every element |
| Shadows | None at all, or 2–3 stacked offsets at 4–10% alpha | One `0 4px 20px rgba(0,0,0,0.3)` on every box; coloured glows |
| Motion | 150–200ms ease on hover/focus only | Entrance animations on every element; float/pulse loops |
| Density | Tight interiors, large gaps between bands (Vercel: "large gaps + tight interior, never the other way") | Uniform generous padding, so nothing groups |
| Iconography | One size (Vercel logos 24px, Raycast tiles 48–64px), one stroke weight, monochrome | Mixed sizes, mixed styles, emoji standing in for icons |
| Hierarchy | Surface ladder: canvas → surface-1 → surface-2 (Linear, Raycast) | Everything on one plane, differentiated only by shadow |

---

## 3. Copy-paste rules block

```
- Use exactly one accent colour. It may appear on primary CTAs, links, and focus rings only — never as a
  section background, card fill, or body text colour.
- Never use #000000 or #ffffff for text. Light mode ink #171717–#292827; dark mode ink #f4f4f6–#f7f8f8.
- Never use #000000 as a dark canvas. Use #07080a–#0f1011.
- Light canvas is off-white (#fafafa–#f6f5f4), not pure white. Cards lift to pure white on top of it.
- Define exactly 3 text tiers: primary ink, muted (~60% contrast), subtle (~40%). Use them consistently.
- Body text 16px, weight 400, line-height 1.5–1.6.
- Display/headings: weight 600 maximum. Never 700 or 800.
- Headline line-height 1.0–1.15; letter-spacing −2.5% to −3% of font size (e.g. −1.4px at 48px). Body
  letter-spacing 0.
- Use at most 8 font sizes across the page: 12, 14, 16, 18, 22, 28, 40, 56.
- Sentence case for headings and eyebrows. No ALL-CAPS tracked labels.
- All spacing must be a multiple of 4px. Preferred set: 4, 8, 12, 16, 24, 32, 48, 96.
- 96px vertical gap between major sections; 24px padding inside cards; 8px gap between a heading and
  its paragraph. Large gaps outside, tight gaps inside.
- Centre content in a max-width of 1200–1280px with 24px gutters.
- Border radius: 8px on buttons and inputs, 12px on cards, 16px on image/screenshot frames. Pills
  (9999px) only for tags and toggles. Nothing else, ever.
- Every card gets a 1px hairline border about 6% off the canvas colour (#ebebeb light, #23252a dark).
- Shadows: either none (use a lighter surface colour for elevation) or two stacked low offsets,
  e.g. `0 1px 2px rgba(15,15,15,0.04), 0 4px 12px rgba(15,15,15,0.08)`. Never a single large blur.
- No glows, no blur backdrops, no glassmorphism, no gradient text.
- At most one decorative gradient per page, in the hero band only, at full-bleed scale.
- Buttons: 14–16px text at weight 500, padding 8–10px vertical / 14–18px horizontal, min height 36px
  (44px for touch).
- Transitions 150ms ease on background-color, border-color, opacity and transform only. No entrance
  animations, no infinite loops. Respect `prefers-reduced-motion`.
- One primary button per section band. Everything else is secondary or a text link.
- Icons: one size (16px inline, 20px in cards, 24px in logos), one stroke width (1.5px), inline SVG,
  currentColor. Never emoji as icons or section markers.
- Left-align body copy and card content. Centre only the hero and a closing CTA band.
- Never invent statistics, logos, testimonials, or chart data. Omit the section instead.
```

---

## 4. Anti-patterns and why each reads as AI-generated

- **Purple/blue gradient hero.** It is the default of every no-code template and LLM demo. The seven use
  brand-specific atmosphere only (Stripe's mesh, Raycast's red stripe) and confine it to one band.
  A generic indigo→violet wash signals "no brand existed, so a decoration was substituted."
- **Glassmorphism (`backdrop-filter: blur`).** None of the seven use it. It is expensive to render, wrecks
  text contrast, and appears because it looks impressive in isolation rather than in a system.
- **Glow effects / coloured drop shadows.** Real systems use shadows to imply light from above at 4–10%
  alpha. A glow implies a light source *inside* the element, which never happens physically — it reads
  as decoration for its own sake.
- **Everything in a card.** Cards signal "this is a discrete, liftable object." When headers, paragraphs
  and footers are all carded, the signal is destroyed and the page becomes a stack of boxes. Linear and
  Intercom lift only cards that represent parallel choices (pricing, features).
- **20–32px radius everywhere.** The seven cap at 16px for chrome. Large uniform radii make everything
  look like a mobile-app widget and eliminate the button-vs-card distinction that 8-vs-12px provides.
- **Fake statistics ("10,000+ teams", "99.9% uptime").** Unverifiable and unattributed, and the numbers
  are always round. Professional pages either cite a named customer or show product UI instead.
- **Meaningless charts.** A sparkline with no axis, units or source is ornament pretending to be data.
  Real dashboards label axes; decorative ones cannot.
- **Emoji as section markers (🚀 ✨ 🔥).** No design system in the reference set uses emoji anywhere.
  They render differently per platform, cannot inherit colour, and cannot be sized on the icon scale —
  so they are unmistakably a text-model artifact.
- **Everything centred.** Centred text has a ragged left edge, so multi-line paragraphs lose their
  scanning anchor. Raycast, Linear and Vercel left-align all section content; centring is reserved for
  hero and closing CTA.
- **Inconsistent icon sizes/styles.** Mixing 16px outline with 32px filled with an emoji shows there was
  no icon system. Vercel pins all customer logos to exactly 24px height.
- **Excessive pills/badges.** Pills mark status and taxonomy. A page where every noun is a pill has no
  remaining way to mark actual status, and the rounded shapes fight the 8/12px rectangle rhythm.
- **Three-column feature grid with generic icon + heading + one sentence, repeated three times.** The
  structural fingerprint of generated marketing copy. Vary block shapes: one wide row, one 2-up, one list.

---

## 5. Single-file constraints (no webfonts, no CDN, no build)

**The substitution problem.** All seven specs name **Inter** as the open-source stand-in for their
proprietary face (Linear: "Inter at 500/600/700"; Stripe: "Inter at weight 300"; Intercom: "Inter at
weight 500"; Superhuman: "Inter Variable"; Vercel: "Inter … closest stylistic match"; Raycast *is*
Inter). Under a strict CSP you cannot load Inter either — so use the local system stack and adjust for it.

**Stacks that actually look good with nothing loaded:**

```css
/* UI / body — resolves to SF Pro on macOS/iOS, Segoe UI Variable on Win11, Roboto on Android */
--font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI Variable Text", "Segoe UI",
             system-ui, Roboto, "Helvetica Neue", Arial, sans-serif;

/* Display — Segoe UI Variable Display is optically sized for large text on Win11 */
--font-display: -apple-system, BlinkMacSystemFont, "Segoe UI Variable Display", "Segoe UI",
                system-ui, Roboto, sans-serif;

/* Mono — for eyebrows, code, technical labels (Vercel/Linear pattern) */
--font-mono: ui-monospace, SFMono-Regular, "SF Mono", "Cascadia Mono", Menlo, Consolas,
             "Liberation Mono", monospace;

/* Editorial/serif alternative when a page should not look like a SaaS app */
--font-serif: ui-serif, Georgia, "Iowan Old Style", "Times New Roman", serif;
```

**Adjustments when the real typeface is unavailable:**

- **Reduce negative tracking to about −2% of font size, not −4%.** SF Pro and Segoe UI Variable already
  tighten optically at display sizes; Linear's −3.75% applied to system fonts collides glyphs.
- **Use weight 600, not 500, for display.** Intercom and Superhuman rely on custom in-between weights
  (500, 460, 540) that system stacks cannot hit — the nearest safe rendering is 600. Stripe's 300 is
  reproducible on macOS but renders as Light and inconsistent on Windows; avoid weights below 400.
- **Do not chase the brand's exact colours.** The transferable part is the *structure* (one accent,
  three ink tiers, hairline at 6%), not the specific hex. Pick a hue, then build the ladder.
- **Set `font-feature-settings: "tnum"` on any numeric column or price** — costs nothing, works on
  system fonts, and is Stripe's explicit signature detail.
- Add `-webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;` — light text on dark
  canvases (Linear, Raycast) looks heavy without it.
- **Replace unavailable illustration with structure**, exactly as the seven replace it with product
  screenshots: build a faux UI panel from divs (a titlebar row, hairline dividers, mono text, keycap
  glyphs à la Raycast) inside a 16px-radius frame. This reads as product, not as decoration, and
  needs no assets.
- Icons must be inline SVG with `stroke="currentColor" stroke-width="1.5" fill="none"`, on a single
  size scale. No icon-font CDN, no emoji fallback.
- Support both themes with `@media (prefers-color-scheme: dark)` over CSS custom properties; the seven
  split evenly between light-first (Stripe, Notion, Vercel, Superhuman, Intercom) and dark-only
  (Linear, Raycast), and both are professional — the failure mode is a half-converted theme.
