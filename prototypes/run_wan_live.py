"""run_wan_live.py — submit wan_optimized_12gb.json to the live ComfyUI (:8188) and wait for the mp4.

    python run_wan_live.py --no-rife     # first test: VideoCombine straight off the tiled decode
    python run_wan_live.py               # full graph incl. RIFE VFI (needs Frame-Interpolation deps)

--no-rife skips the RIFE node so ComfyUI-Frame-Interpolation's extra deps (kornia/opencv-contrib)
aren't required to prove the core Wan diffusion -> tiled decode -> mp4 pipeline runs on 12 GB.
"""
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

COMFY = "http://127.0.0.1:8188"
WF = Path(__file__).with_name("wan_optimized_12gb.json")


def _get(u, t=30):
    with urllib.request.urlopen(u, timeout=t) as r:
        return json.load(r)


def _post(u, o, t=60):
    req = urllib.request.Request(u, data=json.dumps(o).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=t) as r:
        return json.load(r)


def main():
    wf = json.loads(WF.read_text(encoding="utf-8"))
    if "--no-rife" in sys.argv:
        wf["14"]["inputs"]["images"] = ["12", 0]   # VideoCombine <- VAEDecodeTiled (bypass RIFE)
        wf["14"]["inputs"]["frame_rate"] = 16
        wf.pop("13", None)
        print("mode: --no-rife (core pipeline only)")

    try:
        pid = _post(f"{COMFY}/prompt", {"prompt": wf, "client_id": uuid.uuid4().hex})["prompt_id"]
    except urllib.error.HTTPError as e:
        print("PROMPT REJECTED:", e.read().decode()[:2500])
        return 1
    print("queued", pid)

    t0 = time.time()
    while time.time() - t0 < 2400:
        h = _get(f"{COMFY}/history/{pid}")
        if pid in h:
            outs = h[pid].get("outputs", {})
            status = h[pid].get("status", {})
            print(f"\nFINISHED in {time.time()-t0:.0f}s  status={status.get('status_str')}")
            print(json.dumps(outs, indent=1)[:1500])
            return 0
        print(f"...{time.time()-t0:.0f}s", flush=True)
        time.sleep(10)
    print("TIMEOUT (still running after 40min)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
