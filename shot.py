"""shot.py — render a web page or local HTML file to a PNG. Stdlib only.

Modular by design: no imports from the rest of trawl, no pip installs. Copy this one
file into any project. It shells out to the Chromium-family browser already on the
machine (Edge ships with Windows), the same browsers trawl's launch.ps1 uses.

This is the lazy stand-in for PixelRAG's `pixelshot`: instead of Playwright + a
Chromium download + Node, it drives the installed browser's headless --screenshot.

    from shot import capture
    capture("https://example.com", "example.png")
    capture(r"C:\...\poc.html", "poster.png", width=1080, height=1350)

    python shot.py <url-or-file> [out.png] [--width W] [--height H] [--wait MS]

ponytail: viewport capture at a fixed window size, not true full-page stitching.
Full-page needs CDP (a websocket + Page.captureScreenshot) — the heavy path this
file exists to avoid. Set a tall --height to capture long pages. Upgrade to CDP
only if fixed-height framing measurably falls short.
"""
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

# Same discovery order as launch.ps1, plus the per-user Edge install.
_CANDIDATES = [
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
]


def find_browser():
    """First installed Edge/Chrome, or None. Override with $SHOT_BROWSER."""
    override = os.environ.get("SHOT_BROWSER")
    if override and Path(override).is_file():
        return override
    for c in _CANDIDATES:
        p = os.path.expandvars(c)
        if Path(p).is_file():
            return p
    return None


def _as_url(target):
    """Pass through http(s)/file/data URLs; turn a local path into a file:// URL."""
    if urlparse(str(target)).scheme in ("http", "https", "file", "data"):
        return str(target)
    return Path(target).resolve().as_uri()


def capture(target, out, width=1280, height=2000, wait=1500, browser=None, timeout=60,
            transparent=False):
    """Render `target` (URL or local file) to PNG `out`. Returns the output path.

    wait        — ms of virtual time for the page to settle/render before capture.
    transparent — capture with a real alpha channel (page background must be transparent);
                  needed to overlay an HTML type layer onto video. Raises RuntimeError if no
                  browser is found or the PNG isn't produced.
    """
    browser = browser or find_browser()
    if not browser:
        raise RuntimeError(
            "No Edge/Chrome found. Install one, or set SHOT_BROWSER to its .exe path."
        )
    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()  # stale file would masquerade as success below

    # A throwaway profile: avoids locking/attaching to a running browser instance.
    profile = Path(os.environ.get("TEMP", ".")) / "shot-profile"
    args = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
        f"--user-data-dir={profile}",
        f"--window-size={int(width)},{int(height)}",
        f"--virtual-time-budget={int(wait)}",
        f"--screenshot={out}",
    ]
    if transparent:
        args.append("--default-background-color=00000000")  # true alpha instead of white plate
    args.append(_as_url(target))
    try:
        subprocess.run(args, timeout=timeout, capture_output=True)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Browser timed out after {timeout}s rendering {target}")

    # Headless exit codes are unreliable across versions; trust the artifact instead.
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(f"No screenshot produced for {target} (empty or missing {out})")
    return str(out)


def demo():
    """Self-check: render inline HTML and assert a real PNG lands. Needs a browser."""
    if not find_browser():
        print("SKIP demo: no browser installed")
        return
    html = "data:text/html," + "<h1 style='font:80px sans-serif'>shot.py works</h1>"
    out = capture(html, Path(os.environ.get("TEMP", ".")) / "shot-demo.png", 400, 200)
    size = Path(out).stat().st_size
    assert size > 500, f"PNG suspiciously small: {size} bytes"
    print(f"OK demo -> {out} ({size} bytes)")


def _main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: python shot.py <url-or-file> [out.png] "
              "[--width W] [--height H] [--wait MS]")
        return 0
    if argv[0] == "demo":
        demo()
        return 0
    target = argv[0]
    out = "shot.png"
    kw = {}
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--width":
            kw["width"] = int(argv[i + 1]); i += 2
        elif a == "--height":
            kw["height"] = int(argv[i + 1]); i += 2
        elif a == "--wait":
            kw["wait"] = int(argv[i + 1]); i += 2
        else:
            out = a; i += 1
    print(capture(target, out, **kw))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
