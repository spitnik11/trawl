"""Contract tests for idea.generate().  python tests/test_idea.py

No test framework — stdlib only, same rule as the rest of the project. These assert the output
contract other code depends on (trawl.py /api/generate, review.py queue), not the wording of any
one hook. generate() draws at RANDOM, so tests check invariants, never byte-identical output.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
import idea  # noqa: E402

# Business pool (pain/build material). generate(...,"linkedin"/"twitter") draws from these.
FIXTURE = [
    {"id": "hn:1", "title": "Ask HN: how do I build a stock trading bot without a broker API?",
     "body": "I want to automate my portfolio and backtest a strategy.",
     "source": "hackernews", "url": "u1", "score": 120, "comments": 40, "kind": "business"},
    {"id": "so:4", "title": "How do I deploy a website to a live server for the first time?",
     "body": "I have HTML and CSS and a domain and no idea what hosting means.",
     "source": "stackexchange/stackoverflow", "url": "u4", "score": 40, "comments": 15, "kind": "business"},
    {"id": "so:7", "title": "Best way to start learning SQL for a monthly sales report?",
     "body": "I want to query my database and build reports and invoices.",
     "source": "stackexchange/stackoverflow", "url": "u7", "score": 20, "comments": 8, "kind": "business"},
    {"id": "rd:6", "title": "Best way to automate client scheduling and invoicing cheaply",
     "body": "A small business booking and invoice tool.",
     "source": "reddit", "url": "u6", "score": 55, "comments": 18, "kind": "business"},
]

# AI-feature pool (new models/tools). generate(...,"aifeature") draws from these.
AI_FIXTURE = [
    {"id": "hf:a", "title": "Laguna S 2.1", "body": "trending text-generation model, GGUF.",
     "source": "huggingface", "url": "hu1", "score": 12, "comments": 0, "kind": "ai-feature"},
    {"id": "gh:b", "title": "omnigent", "body": "open-source AI agent framework.",
     "source": "github", "url": "gu1", "score": 7000, "comments": 30, "kind": "ai-feature"},
    {"id": "arxiv:c", "title": "3D-Aware VLMs with Implicit Geometry", "body": "a new vision-language paper.",
     "source": "arxiv", "url": "au1", "score": 0, "comments": 0, "kind": "ai-feature"},
]

BUSINESS_KEYS = {"id", "hook", "hook_free", "hook_promo", "artifact", "category", "keyword",
                 "angle", "sources", "strength", "kind", "post_type", "style", "mode"}

FAILURES = []


def check(cond, msg):
    print(f"  {'ok  ' if cond else 'FAIL'} {msg}")
    if not cond:
        FAILURES.append(msg)


def test_business_types():
    for ptype, mode_hook in (("linkedin", "hook_edu"), ("twitter", "hook_promo")):
        print(ptype)
        specs = idea.generate(FIXTURE, ptype, 3)
        check(1 <= len(specs) <= 3, f"{ptype}: 1-3 specs from a 4-item pool (got {len(specs)})")
        for s in specs:
            check(BUSINESS_KEYS <= set(s), f"{ptype}: has all contract keys on {s['id']}")
            check(bool(s["hook"].strip()), f"{ptype}: hook non-empty on {s['id']}")
            check(s["style"] == ptype and s["post_type"] == ptype, f"{ptype}: style/type tagged")
            check(s["keyword"].isupper() and " " not in s["keyword"], f"{ptype}: keyword one uppercase word")
            # the top-level hook must match the mode's voice: educational for linkedin (teach-first,
            # no "comment KEYWORD" gate since 2026-07-27), promo for twitter. See idea.generate().
            check(s["hook"] == s[mode_hook], f"{ptype}: top-level hook matches its mode ({mode_hook})")
            check(not re.search(r"\$[\d,]{3,}", s["hook_promo"]), f"{ptype}: no invented income figure")
        ids = [s["id"] for s in specs]
        check(len(ids) == len(set(ids)), f"{ptype}: no duplicate specs")


def test_ai_feature():
    print("aifeature")
    specs = idea.generate(AI_FIXTURE, "aifeature", 3)
    check(1 <= len(specs) <= 3, f"1-3 ai-feature specs (got {len(specs)})")
    subjects = {i["title"] for i in AI_FIXTURE}
    for s in specs:
        check(s["kind"] == "ai-feature", f"kind is ai-feature on {s['id']}")
        check(bool(s["hook"].strip()), f"hook non-empty on {s['id']}")
        check(s.get("subject") in subjects, f"subject is a real scraped item on {s['id']}")
        # ai-feature is educational, no reply-keyword gate in the hook
        check("reply new" not in s["hook"].lower(), f"no reply-keyword gate on {s['id']}")


def test_edges():
    print("edges")
    check(idea.generate([], "linkedin", 3) == [], "empty pool -> empty output, no crash")
    check(len(idea.generate(FIXTURE, "twitter", 99)) <= len(FIXTURE), "n over pool size is capped")
    check(len(idea.generate(FIXTURE, "unknown_type", 1)) == 1, "unknown type falls back, still produces")
    weird = idea.generate([{"id": "x:1", "title": "", "body": "", "source": "", "url": "",
                            "score": 0, "comments": 0, "kind": "business"}], "linkedin", 1)
    check(all(w["artifact"].strip() for w in weird), "blank title still yields a concrete artifact")


if __name__ == "__main__":
    test_business_types()
    test_ai_feature()
    test_edges()
    print(f"\n{'PASS' if not FAILURES else 'FAIL: ' + str(len(FAILURES))}")
    sys.exit(1 if FAILURES else 0)
