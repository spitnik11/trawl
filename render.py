"""render.py — the VISUAL LAYER for Trawl ads (Rung 2). Stdlib only.

Turns an ad brief into a model-grade image prompt (the "legit prompt tuning"), renders a still
on the local ComfyUI (:8188) by reusing Z-Image Studio's verified z-image-turbo-api workflow,
and best-effort verifies the render against the brief via PromptLens's Florence-2 (:8000).

Talks to both services over plain HTTP (urllib) — no pip, no cross-venv import. The compiler
PORTS PromptLens's methodology (family -> natural/tag serializer + model-aware negatives) in a
compact form rather than depending on the running service.

    from render import compile_prompt, render_still, verify_still
    pos, neg = compile_prompt(brief)
    png = render_still(brief, "out")
    ok, note = verify_still(png, brief)
"""
from __future__ import annotations

import json
import mimetypes
import random
import shutil
import time
import urllib.request
import uuid
from pathlib import Path

COMFY = "http://127.0.0.1:8188"
PROMPTLENS = "http://127.0.0.1:8000"
TEMPLATE = Path(r"Z:\codex app\workflows\z-image-turbo-api.json")

# The exported blueprint carries placeholder loader names; these are the ACTUAL installed
# Z-Image Turbo files (from the live ComfyUI object_info). Overridden onto the template at
# submit time so the shared template file is never edited. Swap to change model.
UNET_NAME = "zImageTurbo_turbo.safetensors"
CLIP_NAME = "qwen3_4b.safetensors"
VAE_NAME = "flux1AE_v10.safetensors"

# PromptLens vision provider for the verify gate. "florence2" = real caption (downloaded on first
# use, slow); "fixture" = mock (fast, useless for real matching). "joycaption" = deep/slower.
VERIFY_PROVIDER = "florence2"

# aspect -> (ad canvas W,H) [dup of ad.ASPECTS to avoid a circular import] and the
# render latent size (multiples of 16, short edge ~768-1024) scaled up to canvas by the graph.
ASPECTS = {"1:1": (1080, 1080), "4:5": (1080, 1350), "9:16": (1080, 1920), "16:9": (1920, 1080)}
LATENT = {"1:1": (1024, 1024), "4:5": (896, 1152), "9:16": (768, 1344), "16:9": (1280, 704)}

# ============================================================================
# AD VOCAB PACK — Creatify visual_direction -> concrete, testable prompt fragments.
# This is the tuning surface: edit fragments here, re-render, compare. Curated, not free-text.
# ============================================================================

AD_VOCAB = {
    "Hero Product": {
        "comp": "a single hero product centered and floating on a seamless backdrop",
        "cam": "85mm macro product photography, shallow depth of field",
        "light": "soft studio softbox with a crisp rim light", "mood": "premium, clean, minimal"},
    "Lifestyle": {
        "comp": "the product in use in a real lived-in environment, candid moment",
        "cam": "35mm reportage, natural perspective", "light": "warm golden-hour window light",
        "mood": "aspirational, authentic, warm"},
    "Flat Lay": {
        "comp": "an overhead flat lay, product with complementary objects arranged on a surface",
        "cam": "top-down 50mm, even framing", "light": "bright soft diffused daylight",
        "mood": "editorial, tidy, considered"},
    "Before/After": {
        "comp": "a clean split composition contrasting two states side by side",
        "cam": "50mm, symmetrical framing", "light": "even neutral studio light",
        "mood": "clear, evidential"},
    "Infographic": {
        "comp": "a bold data-visual scene, oversized abstract chart forms and glowing metrics",
        "cam": "isometric 3d render, clean vector shapes", "light": "flat even key light",
        "mood": "confident, informative, techy"},
    "Testimonial Card": {
        "comp": "a portrait of a happy real customer, plenty of negative space for a quote",
        "cam": "85mm portrait, soft bokeh", "light": "flattering soft key with gentle fill",
        "mood": "trustworthy, human, warm"},
    "Comparison": {
        "comp": "two options weighed against each other, the winner subtly emphasised",
        "cam": "50mm, balanced two-up framing", "light": "neutral even studio light",
        "mood": "honest, decisive"},
    "Text-Heavy": {
        "comp": "a bold minimal backdrop with deep negative space reserved for large type",
        "cam": "clean flat composition, generous margins", "light": "soft directional gradient light",
        "mood": "editorial, high-contrast, modern"},
    "UGC Screenshot": {
        "comp": "an authentic phone-shot moment, slightly imperfect framing, real hands",
        "cam": "smartphone camera, casual angle", "light": "available indoor light",
        "mood": "raw, relatable, unpolished"},
    "Ingredient/Feature": {
        "comp": "a dramatic close-up spotlighting one key ingredient or feature, exploded detail",
        "cam": "100mm macro, extreme detail", "light": "focused spotlight with dark falloff",
        "mood": "premium, scientific, focused"},
}

# model family -> serializer + anchors/negatives (ported from PromptLens's approach)
FAMILY = {
    "natural": {  # Z-Image Turbo / Krea 2 — photographic natural language
        "anchor": "cinematic commercial photograph, high detail, sharp focus, 4k, professional",
        "neg": "text, watermark, logo, letters, words, blurry, low quality, distorted, deformed, "
               "extra limbs, jpeg artifacts, oversaturated"},
    "tag": {      # Illustrious XL — danbooru tags
        "anchor": "masterpiece, best quality, highly detailed, absurdres",
        "neg": "text, watermark, signature, lowres, bad anatomy, worst quality, jpeg artifacts"},
}
MODEL_FAMILY = {"z-image": "natural", "krea": "natural", "illustrious": "tag"}


def compile_prompt(brief: dict) -> tuple[str, str]:
    """Brief -> (positive, negative) tuned for the routed model family. The 'legit' compile."""
    style = brief.get("visual_direction", "Text-Heavy")
    v = AD_VOCAB.get(style, AD_VOCAB["Text-Heavy"])
    fam = MODEL_FAMILY.get(brief.get("visual_model", "z-image"), "natural")
    f = FAMILY[fam]
    subject = brief.get("visual_subject") or "a sleek modern product"
    palette = brief.get("brand", {}).get("palette_words", "deep charcoal with a single vivid accent")

    if fam == "tag":
        parts = [subject, v["comp"], v["cam"], v["light"], v["mood"], palette, f["anchor"]]
        positive = ", ".join(p.strip() for p in parts if p.strip())
    else:
        positive = (f"{f['anchor']} of {subject}. {v['comp'].capitalize()}. "
                    f"{v['cam'].capitalize()}, {v['light']}. "
                    f"Colour palette: {palette}. Mood: {v['mood']}.")
    return positive, f["neg"]


# ============================================================================
# COMFYUI CLIENT — reuse the verified z-image-turbo-api template, swap text/seed/size.
# ============================================================================

def _get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def _post_json(url, obj, timeout=30):
    data = json.dumps(obj).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


class Cancelled(Exception):
    """Raised when a render is cancelled cooperatively (user hit Cancel)."""


def interrupt():
    """Best-effort: tell ComfyUI to stop the current render immediately."""
    try:
        _post_json(f"{COMFY}/interrupt", {}, timeout=5)
    except Exception:
        pass


def comfy_up(retries=3) -> bool:
    for _ in range(retries):                 # ComfyUI stalls its HTTP server during heavy render/GC
        try:
            _get(f"{COMFY}/system_stats", timeout=10); return True
        except Exception:
            time.sleep(2)
    return False


def render_still(brief: dict, outdir="out", timeout_s=240, should_cancel=None) -> str:
    """Render the visual layer on ComfyUI. Returns the PNG path. Raises if ComfyUI is down."""
    if not comfy_up():
        raise RuntimeError(f"ComfyUI not reachable at {COMFY} — start it (Z-Image Studio) first")
    if not TEMPLATE.exists():
        raise RuntimeError(f"render template missing: {TEMPLATE}")

    aspect = brief.get("aspect", "16:9")
    positive, _neg = compile_prompt(brief)
    seed = brief.get("seed") or random.randint(1, 2**31 - 1)
    lw, lh = LATENT.get(aspect, (1024, 1024))
    aw, ah = ASPECTS.get(aspect, (1920, 1080))

    g = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    g["1"]["inputs"]["unet_name"] = UNET_NAME       # real installed filenames (template has placeholders)
    g["2"]["inputs"]["clip_name"] = CLIP_NAME
    g["3"]["inputs"]["vae_name"] = VAE_NAME
    g["4"]["inputs"]["text"] = positive             # positive prompt (neg stays zeroed: Z-Image CFG1)
    g["8"]["inputs"]["seed"] = seed
    g["6"]["inputs"]["width"], g["6"]["inputs"]["height"] = lw, lh
    g["11"]["inputs"]["width"], g["11"]["inputs"]["height"] = aw, ah
    g["10"]["inputs"]["filename_prefix"] = "trawl-ad"

    cid = uuid.uuid4().hex
    pid = _post_json(f"{COMFY}/prompt", {"prompt": g, "client_id": cid})["prompt_id"]

    deadline = time.monotonic() + timeout_s
    img = None
    while time.monotonic() < deadline:
        if should_cancel and should_cancel():
            interrupt(); raise Cancelled()
        try:
            hist = _get(f"{COMFY}/history/{pid}", timeout=10)
        except Exception:
            time.sleep(1.5); continue
        if pid in hist:
            outs = hist[pid].get("outputs", {})
            imgs = outs.get("10", {}).get("images", [])
            if imgs:
                img = imgs[0]; break
        time.sleep(1.5)
    if not img:
        raise RuntimeError(f"ComfyUI render timed out after {timeout_s}s (prompt {pid})")

    q = urllib.parse.urlencode({"filename": img["filename"], "subfolder": img.get("subfolder", ""),
                                "type": img.get("type", "output")})
    out = Path(outdir).resolve() / f"visual_{seed}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(f"{COMFY}/view?{q}", timeout=30) as r:
        out.write_bytes(r.read())
    return str(out)


# ============================================================================
# WAN MOTION B-ROLL — reuse the validated 12GB workflow, inject prompt/size/seed.
# ============================================================================

WAN_TEMPLATE = Path(r"Z:\Claude app\trawl\prototypes\wan_optimized_12gb.json")
WAN_FPS = 16                                   # Wan 2.2 14B native
WAN_SIZE = {"16:9": (1024, 576), "1:1": (768, 768), "4:5": (768, 960), "9:16": (576, 1024)}
WAN_NEG = ("blurry, low quality, jpeg artifacts, watermark, text, letters, distorted, morphing, "
           "warping, flicker, jitter, static image, deformed, extra limbs, oversaturated")


def rife_available() -> bool:
    """True if ComfyUI-Frame-Interpolation's RIFE VFI node is registered (deps installed)."""
    try:
        return "RIFE VFI" in _get(f"{COMFY}/object_info", timeout=30)
    except Exception:
        return False


def wan_length(duration_s: float) -> int:
    """Frames for a clip, snapped to Wan's 4n+1 cadence (min 9)."""
    k = round(duration_s * WAN_FPS)
    return max(9, round((k - 1) / 4) * 4 + 1)


def compile_motion_prompt(brief: dict) -> str:
    """Ad brief -> a Wan t2v motion prompt (subject → scene → camera move → light → style)."""
    style = brief.get("visual_direction", "Text-Heavy")
    v = AD_VOCAB.get(style, AD_VOCAB["Text-Heavy"])
    subject = brief.get("visual_subject") or "a sleek modern product"
    motion = brief.get("motion") or "slow cinematic push-in, smooth steady camera move"
    palette = brief.get("brand", {}).get("palette_words", "deep charcoal with a single vivid accent")
    # order per research: subject -> scene -> ONE camera move (+pace) -> light -> palette -> texture.
    # texture words fight Wan's plastic default; keep to a single, non-contradictory camera move.
    return (f"cinematic product commercial b-roll, {subject}, {v['comp']}, {motion}, {v['light']}, "
            f"colour palette {palette}, {v['mood']}, shallow depth of field, smooth natural motion, "
            f"subtle film grain, slight motion blur, glossy realistic texture, filmic, high detail, 4k")


def render_broll(brief: dict, outdir="out", timeout_s=900, should_cancel=None) -> str:
    """Render a Wan 2.2 motion clip. Returns the mp4 path. Uses the no-RIFE path (16fps native)."""
    if not comfy_up():
        raise RuntimeError(f"ComfyUI not reachable at {COMFY} — start it first")
    if not WAN_TEMPLATE.exists():
        raise RuntimeError(f"Wan workflow missing: {WAN_TEMPLATE} (run generate_wan_12gb_workflow.py)")

    aspect = brief.get("aspect", "16:9")
    w, h = WAN_SIZE.get(aspect, (1024, 576))
    length = wan_length(brief.get("duration", 3.0))
    seed = brief.get("wan_seed") or random.randint(1, 2**31 - 1)

    g = json.loads(WAN_TEMPLATE.read_text(encoding="utf-8"))
    g["7"]["inputs"]["text"] = compile_motion_prompt(brief)
    g["8"]["inputs"]["text"] = WAN_NEG
    g["9"]["inputs"]["width"], g["9"]["inputs"]["height"], g["9"]["inputs"]["length"] = w, h, length
    g["10"]["inputs"]["noise_seed"] = seed
    g["11"]["inputs"]["noise_seed"] = seed
    g["14"]["inputs"]["filename_prefix"] = "trawl-broll"
    if not rife_available():
        # bypass RIFE: VideoCombine straight off the tiled decode (native 16fps)
        g["14"]["inputs"]["images"] = ["12", 0]
        g["14"]["inputs"]["frame_rate"] = WAN_FPS
        g.pop("13", None)
    # else: leave the template's RIFE x2 path (node 13 -> VideoCombine @ 32fps) intact

    pid = _post_json(f"{COMFY}/prompt", {"prompt": g, "client_id": uuid.uuid4().hex})["prompt_id"]
    deadline = time.monotonic() + timeout_s
    src = None
    while time.monotonic() < deadline:
        if should_cancel and should_cancel():
            interrupt(); raise Cancelled()
        try:                                    # ComfyUI can stall the HTTP server while loading 9GB
            hist = _get(f"{COMFY}/history/{pid}", timeout=15)
        except Exception:
            time.sleep(3); continue
        if pid in hist:
            gifs = hist[pid].get("outputs", {}).get("14", {}).get("gifs", [])
            if gifs:
                src = gifs[0].get("fullpath"); break
        time.sleep(3)
    if not src:
        raise RuntimeError(f"Wan render timed out after {timeout_s}s (prompt {pid})")

    out = Path(outdir).resolve() / f"broll_{seed}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, out)
    return str(out)


# ============================================================================
# VERIFY GATE — best-effort Florence-2 check via PromptLens. NON-BLOCKING by design.
# ============================================================================

def _multipart(fields_files):
    boundary = "----trawl" + uuid.uuid4().hex
    body = b""
    for name, filename, content in fields_files:
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        body += f"Content-Type: {ctype}\r\n\r\n".encode() + content + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def _texts(obj, acc):
    """Recursively pull observation/caption text out of an unknown-shaped analysis response."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("text", "caption", "description") and isinstance(v, str):
                acc.append(v)
            else:
                _texts(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _texts(v, acc)


def verify_still(png_path: str, brief: dict, min_overlap=2):
    """(ok, note). Captions the render (Florence-2) and checks it covers the brief's subject words.
    Any failure/unreachability -> (True, 'skipped: ...') so the pipeline never blocks on it."""
    try:
        content = Path(png_path).read_bytes()
        body, ctype = _multipart([("file", Path(png_path).name, content)])
        req = urllib.request.Request(f"{PROMPTLENS}/api/images", data=body, headers={"Content-Type": ctype})
        img = json.load(urllib.request.urlopen(req, timeout=30))
        image_id = img.get("id") or img.get("image_id")
        if not image_id:
            return True, f"skipped: no image id in upload response ({list(img)[:4]})"

        analysis = _post_json(f"{PROMPTLENS}/api/images/{image_id}/analyze",
                              {"provider_id": VERIFY_PROVIDER}, timeout=600)
        caps = []
        _texts(analysis, caps)
        caption = " ".join(caps).lower()
        if not caption:
            return True, "skipped: analyzer returned no caption text"

        subj = (brief.get("visual_subject", "") + " " + brief.get("visual_direction", "")).lower()
        words = {w for w in "".join(c if c.isalnum() else " " for c in subj).split() if len(w) > 3}
        hits = sorted(w for w in words if w in caption)
        ok = len(hits) >= min_overlap
        return ok, f"caption matched {len(hits)}/{len(words)} key words {hits[:6]}"
    except Exception as e:
        return True, f"skipped: verify unavailable ({type(e).__name__}: {e})"


import urllib.parse  # noqa: E402  (used in render_still; keep near use-free bottom to avoid top clutter)


def _selftest():
    """No services needed: compiler produces family-correct prompts with the vocab baked in."""
    b = {"visual_direction": "Hero Product", "visual_model": "z-image",
         "visual_subject": "a matte-black smart speaker",
         "brand": {"palette_words": "emerald and charcoal"}}
    pos, neg = compile_prompt(b)
    assert "smart speaker" in pos and "hero product" in pos.lower()
    assert "emerald" in pos and "photograph" in pos.lower() and "text" in neg
    # tag family serializes differently (comma tags, booru anchor)
    b["visual_model"] = "illustrious"
    pos2, _ = compile_prompt(b)
    assert "masterpiece" in pos2 and "," in pos2 and "." not in pos2.split("masterpiece")[0][:20]
    print("selftest ok")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print("compile:", compile_prompt({"visual_direction": "Infographic", "visual_model": "z-image",
                                           "visual_subject": "a SaaS analytics dashboard"}))
        print("comfy up:", comfy_up())
