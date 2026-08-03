"""Regression test for the filter gates.  Run:  python tests/test_gates.py

Exists because two audits found the same class of bug in opposite directions:

  v1  patterns too loose  -> 66% junk in the top 50; a company anniversary post ranked #1
  v2  patterns too tight  -> 12 of 16 known-good beginner posts silently deleted

Neither was visible by reading the regexes. Both were obvious the moment real titles were run
through them. Every title below is a real one from an audit, so this file is the contract:
GOOD must all survive, BAD must be dropped or penalised.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
import rank  # noqa: E402

# Must all survive. These are the posts Trawl exists to find.
GOOD = [
    "My journey from zero coding experience to my first working app",
    "Lessons learned building my first app with Claude",
    "Thanks to everyone who helped me debug my first app",
    "I spent 2 years of evenings learning to build with AI",
    "Show HN: I built a recipe app with AI and never wrote a line of code",
    "Show HN: I built an invoice generator as a complete beginner",
    "Show HN: my landing page with a waitlist, built with AI in a weekend",
    "Ask HN: Docker wont start and I have no idea what any of this means",
    "I keep hitting the rate limit on the free tier, what do I do?",
    "Ask HN: my AI keeps saying this package is deprecated, how do I fix it?",
    "Ask HN: Where do I even put my source code so it stays safe?",
    "Show HN: I made a dashboard for my running data",
    'Show HN: Garden Horizons Calculator - Built by a Beginner via "Vibe Coding"',
    "Ask HN: Anyone else struggle with how to learn coding in the AI era?",
    "Last year, all my non-programmer friends built apps",
    "Ask HN: May be a basic question, but how can I use AI well?",
]

# Must be dropped outright, or kept with a junk penalty. Never clean at full score.
BAD = [
    "Thanks HN for 15 years of support and helping me find my life's work",
    "Launch HN: Expanse (YC P26) - Unlock Wasted GPU Capacity",
    "Show HN: Millwright - Rust-based, self-hosted LLM router",
    "Ask HN: Is software engineering still a good career choice for new students?",
    "Ask HN: In the Age of AI, How Do I Grow as a Software Engineer?",
    "Show HN: Margarita - Programming language for Agents",
    "Ask HN: Is it worth learning Vim in 2026?",
    "Werewolf Romance 101: quick trope map and what to watch for",
    "Ask HN: Instagram blocked my new account what can I do?",
    "Show HN: I built Exfault, agentic mobile app pentesting tool",
]


def run(titles):
    items = [{"id": f"t:{i}", "title": t, "body": "", "score": 40,
              "comments": 20, "created_utc": time.time() - 86400}
             for i, t in enumerate(titles)]
    out = rank.score_all(items, verbose=False)
    return {o["title"]: o for o in out}


def main():
    failures = []

    kept = run(GOOD)
    print(f"GOOD  {len(kept)}/{len(GOOD)} survived")
    for t in GOOD:
        if t not in kept:
            failures.append(f"FALSE DROP: {t}")
            print(f"  DROPPED  {t[:70]}")

    kept = run(BAD)
    clean = [t for t in BAD if t in kept and not kept[t]["_why"]["junk"]]
    print(f"BAD   {len(BAD) - len(kept)} dropped, "
          f"{len(kept) - len(clean)} penalised, {len(clean)} unhandled")
    for t in clean:
        failures.append(f"JUNK UNPENALISED: {t}")
        print(f"  UNHANDLED  {t[:70]}")

    if failures:
        print(f"\nFAIL — {len(failures)} problem(s)")
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
