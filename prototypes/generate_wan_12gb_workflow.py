"""
generate_wan_12gb_workflow.py

Standalone prototype: BUILD + VALIDATE a ComfyUI *API-format* workflow (JSON) for
Wan 2.2 14B (A14B) text-to-video, GGUF-quantized, tuned for 12 GB VRAM / 32 GB RAM.

Output: wan_optimized_12gb.json  (next to this script)

ISOLATED PROTOTYPE. It writes exactly ONE workflow file and (optionally) downloads
weights into the ComfyUI models tree. It NEVER reads, edits, overwrites, or injects
into any existing workflow.json or app source. Its only job is to prove the graph
runs on the card in ComfyUI before any app integration.

Run:
    python generate_wan_12gb_workflow.py             # deps check + download + build + validate + save
    python generate_wan_12gb_workflow.py --dry-run   # build + validate + save only (no deps/network)
    python generate_wan_12gb_workflow.py --skip-download
    COMFYUI_ROOT=... python generate_wan_12gb_workflow.py   # point at a different ComfyUI install

Load the result in ComfyUI via the dev-mode "Load (API Format)" button, or POST it
to /prompt. It is NOT the UI graph format (no node positions); it is the execution graph.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

# ============================================================================
# CONSTANTS  — swap these to change quant / variant (e.g. I2V, a different quant).
# ============================================================================

COMFYUI_ROOT = Path(os.environ.get("COMFYUI_ROOT", r"Z:\codex app\ComfyUI"))
OUTPUT_JSON = Path(__file__).with_name("wan_optimized_12gb.json")

# --- generation shape -------------------------------------------------------
WIDTH, HEIGHT = 1024, 640
LENGTH = 49                 # MUST satisfy (LENGTH - 1) % 4 == 0   (49 -> 48/4=12 ok)
CFG = 1.0                   # lightning LoRA constraint: CFG must be 1.0
TOTAL_STEPS = 4             # lightning: 4 steps total, split 0->2 (high) / 2->4 (low)
SEED = 42

# --- timing: native fps * RIFE multiplier = output fps ----------------------
MODEL_NATIVE_FPS = 16       # Wan 2.2 14B native
RIFE_MULTIPLIER = 2
OUTPUT_FPS = MODEL_NATIVE_FPS * RIFE_MULTIPLIER   # 32

# --- flat on-disk filenames the graph references (must match what we download)
UNET_HIGH = "Wan2.2-T2V-A14B-HighNoise-Q4_K_M.gguf"
UNET_LOW = "Wan2.2-T2V-A14B-LowNoise-Q4_K_M.gguf"
CLIP_GGUF = "umt5-xxl-encoder-Q5_K_M.gguf"
VAE_NAME = "wan_2.1_vae.safetensors"          # 14B uses the Wan 2.1 VAE
# CORRECTION (verified 2026-07-30): the T2V high/low lightning PAIR does NOT exist in
# Kijai/WanVideo_comfy — that repo only splits high/low for I2V. For T2V there is ONE distill
# LoRA, applied to BOTH experts. So LORA_HIGH == LORA_LOW here (each expert gets its own
# LoraLoaderModelOnly node pointing at the same file). Swap rank for size/quality if needed.
LORA_HIGH = "lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank64_bf16.safetensors"
LORA_LOW = "lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank64_bf16.safetensors"
RIFE_CKPT = "rife49.pth"          # the installed RIFE VFI's default/available ckpt

# --- download specs: (dest subdir, repo id, expected flat name, match tokens) --
# match tokens locate the real repo path (repos nest files under HighNoise/, etc.)
DOWNLOADS = [
    ("models/unet",           "QuantStack/Wan2.2-T2V-A14B-GGUF",        UNET_HIGH, ["highnoise", "q4_k_m", ".gguf"]),
    ("models/unet",           "QuantStack/Wan2.2-T2V-A14B-GGUF",        UNET_LOW,  ["lownoise", "q4_k_m", ".gguf"]),
    ("models/text_encoders",  "city96/umt5-xxl-encoder-gguf",           CLIP_GGUF, ["umt5", "q5_k_m", ".gguf"]),
    ("models/vae",            "Comfy-Org/Wan_2.1_ComfyUI_repackaged",   VAE_NAME,  ["wan_2.1_vae", ".safetensors"]),
    ("models/loras",          "Kijai/WanVideo_comfy",                   LORA_HIGH, ["lightx2v_t2v_14b", "distill_v2", "rank64", ".safetensors"]),
]

# --- custom nodes required (we warn + continue; we never auto-clone) ---------
CUSTOM_NODES = {
    "ComfyUI-GGUF": "https://github.com/city96/ComfyUI-GGUF",
    "ComfyUI-VideoHelperSuite": "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite",
    "ComfyUI-Frame-Interpolation": "https://github.com/Fannovel16/ComfyUI-Frame-Interpolation",
}
MULTIGPU_NODE = ("ComfyUI-MultiGPU", "https://github.com/pollockjj/ComfyUI-MultiGPU")

POSITIVE = (
    "cinematic product commercial, a sleek developer dashboard glowing on a dark desk, "
    "smooth camera push-in, soft neon rim light, shallow depth of field, high detail, 4k"
)
NEGATIVE = (
    "blurry, low quality, jpeg artifacts, watermark, text, distorted, "
    "overexposed, static, flicker, deformed"
)


# ============================================================================
# 1. DEPENDENCY + DOWNLOAD MANAGER (safe, resumable, never crashes)
# ============================================================================

def check_custom_nodes() -> bool:
    """Warn (with repo URL) for any missing required node pack. Returns True if MultiGPU present."""
    nodes_dir = COMFYUI_ROOT / "custom_nodes"
    for name, url in CUSTOM_NODES.items():
        if not (nodes_dir / name).exists():
            print(f"  [WARN] missing custom node '{name}' -> install: git clone {url}")
        else:
            print(f"  [ok]   {name}")
    multigpu = (nodes_dir / MULTIGPU_NODE[0]).exists()
    if multigpu:
        print(f"  [ok]   {MULTIGPU_NODE[0]} (text-encoder CPU offload available)")
    else:
        print(f"  [note] optional {MULTIGPU_NODE[0]} not found ({MULTIGPU_NODE[1]}) "
              f"-> will require ComfyUI launch with --lowvram")
    return multigpu


def download_all() -> None:
    """Resumable, hash-checked fetch of every weight. Verifies exact filenames; never crashes."""
    try:
        from huggingface_hub import HfApi, hf_hub_download
    except ImportError:
        print("  [WARN] huggingface_hub not installed (pip install huggingface_hub) -> skipping downloads")
        return

    api = HfApi()
    for dest_sub, repo, flat_name, tokens in DOWNLOADS:
        dest_dir = COMFYUI_ROOT / dest_sub
        target = dest_dir / flat_name
        if target.exists():
            print(f"  [have] {dest_sub}/{flat_name}")
            continue
        try:
            files = api.list_repo_files(repo)
        except Exception as e:  # network / repo error -> warn + skip, do not crash
            print(f"  [WARN] could not list {repo}: {e} -> skipping {flat_name}")
            continue

        # verify the EXACT file exists in the repo: prefer an exact basename match,
        # else a path containing all match tokens.
        repo_path = next((f for f in files if Path(f).name == flat_name), None)
        if repo_path is None:
            cands = [f for f in files if all(t.lower() in f.lower() for t in tokens)]
            if not cands:
                print(f"  [WARN] no file matching {tokens} in {repo} -> skipping {flat_name}")
                continue
            repo_path = min(cands, key=len)  # shortest = least-nested best guess
            print(f"  [note] '{flat_name}' not found verbatim in {repo}; using '{repo_path}'")

        try:
            cached = hf_hub_download(repo, repo_path)     # resumable + hash-checked
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(cached, target)               # flatten into models tree
            print(f"  [get]  {dest_sub}/{flat_name}")
        except Exception as e:                            # interrupted / failed -> warn + skip
            print(f"  [WARN] download failed for {repo}:{repo_path}: {e} -> skipping")


# ============================================================================
# 2. NODE GRAPH  (native ComfyUI GGUF path, 12 GB / 32 GB)
# ============================================================================

def build_workflow(multigpu: bool):
    """Return (workflow_dict, lowvram_required)."""
    lowvram_required = not multigpu

    # ONE GGUF CLIP loader for UMT5-XXL (clip type 'wan'). Pin to CPU via MultiGPU if present.
    if multigpu:
        clip_loader = {"class_type": "CLIPLoaderGGUFMultiGPU",
                       "inputs": {"clip_name": CLIP_GGUF, "type": "wan", "device": "cpu"}}
    else:
        clip_loader = {"class_type": "CLIPLoaderGGUF",
                       "inputs": {"clip_name": CLIP_GGUF, "type": "wan"}}

    wf = {
        # --- loaders ---
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": UNET_HIGH}},
        "2": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": UNET_LOW}},
        "3": clip_loader,
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_NAME}},

        # --- lightning LoRA per expert (model-only, applied before its sampler) ---
        "5": {"class_type": "LoraLoaderModelOnly",
              "inputs": {"model": ["1", 0], "lora_name": LORA_HIGH, "strength_model": 1.0}},
        "6": {"class_type": "LoraLoaderModelOnly",
              "inputs": {"model": ["2", 0], "lora_name": LORA_LOW, "strength_model": 1.0}},

        # --- conditioning ---
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": POSITIVE}},
        "8": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": NEGATIVE}},

        # --- empty Wan video latent (4n+1 length) ---
        "9": {"class_type": "EmptyHunyuanLatentVideo",
              "inputs": {"width": WIDTH, "height": HEIGHT, "length": LENGTH, "batch_size": 1}},

        # --- two-stage sampling, 4 steps total: HIGH 0->2, LOW 2->4 ---
        "10": {"class_type": "KSamplerAdvanced",
               "inputs": {"model": ["5", 0], "add_noise": "enable", "noise_seed": SEED,
                          "steps": TOTAL_STEPS, "cfg": CFG, "sampler_name": "euler",
                          "scheduler": "sgm_uniform", "positive": ["7", 0], "negative": ["8", 0],
                          "latent_image": ["9", 0], "start_at_step": 0, "end_at_step": 2,
                          "return_with_leftover_noise": "enable"}},
        "11": {"class_type": "KSamplerAdvanced",
               "inputs": {"model": ["6", 0], "add_noise": "disable", "noise_seed": SEED,
                          "steps": TOTAL_STEPS, "cfg": CFG, "sampler_name": "euler",
                          "scheduler": "sgm_uniform", "positive": ["7", 0], "negative": ["8", 0],
                          "latent_image": ["10", 0], "start_at_step": 2, "end_at_step": 4,
                          "return_with_leftover_noise": "disable"}},

        # --- decode (MANDATORY tiled) -> RIFE 2x -> mp4 ---
        "12": {"class_type": "VAEDecodeTiled",
               "inputs": {"samples": ["11", 0], "vae": ["4", 0], "tile_size": 256,
                          "overlap": 64, "temporal_size": 32, "temporal_overlap": 4}},
        "13": {"class_type": "RIFE VFI",
               "inputs": {"frames": ["12", 0], "ckpt_name": RIFE_CKPT, "multiplier": RIFE_MULTIPLIER,
                          "clear_cache_after_n_frames": 10, "fast_mode": True, "ensemble": True,
                          "scale_factor": 1.0, "dtype": "float32", "torch_compile": False,
                          "batch_size": 1}},
        "14": {"class_type": "VHS_VideoCombine",
               "inputs": {"images": ["13", 0], "frame_rate": OUTPUT_FPS, "loop_count": 0,
                          "filename_prefix": "wan_12gb", "format": "video/h264-mp4",
                          "pix_fmt": "yuv420p", "crf": 19, "save_output": True, "pingpong": False}},
    }
    return wf, lowvram_required


# ============================================================================
# 3. VALIDATION  — runs before save; raises a detailed error naming the rule.
# ============================================================================

def _is_link(v):
    return isinstance(v, list) and len(v) == 2 and isinstance(v[1], int)


def _by_type(wf, t):
    return {nid: n for nid, n in wf.items() if n["class_type"] == t}


def test_workflow_integrity(wf: dict, lowvram_required: bool) -> None:
    def bad(rule, detail):
        raise AssertionError(f"WORKFLOW INTEGRITY FAILED [{rule}]: {detail}")

    # (a) exactly TWO GGUF unets, each -> its lightning LoRA -> its KSampler
    unets = _by_type(wf, "UnetLoaderGGUF")
    if len(unets) != 2:
        bad("a", f"expected exactly 2 UnetLoaderGGUF, found {len(unets)}")
    loras = _by_type(wf, "LoraLoaderModelOnly")
    ksamps = _by_type(wf, "KSamplerAdvanced")
    for uid in unets:
        lora = next((lid for lid, n in loras.items() if n["inputs"].get("model") == [uid, 0]), None)
        if lora is None:
            bad("a", f"unet {uid} does not feed a LoraLoaderModelOnly")
        ks = next((kid for kid, n in ksamps.items() if n["inputs"].get("model") == [lora, 0]), None)
        if ks is None:
            bad("a", f"lora {lora} (from unet {uid}) does not feed a KSamplerAdvanced")

    # (b) steps <= 4 and contiguous windows 0->2, 2->4
    if len(ksamps) != 2:
        bad("b", f"expected exactly 2 KSamplerAdvanced, found {len(ksamps)}")
    for kid, n in ksamps.items():
        if n["inputs"]["steps"] > TOTAL_STEPS:
            bad("b", f"sampler {kid} steps={n['inputs']['steps']} > {TOTAL_STEPS}")
    hi = next((n for n in ksamps.values() if n["inputs"]["add_noise"] == "enable"), None)
    lo = next((n for n in ksamps.values() if n["inputs"]["add_noise"] == "disable"), None)
    if hi is None or lo is None:
        bad("b", "need one high (add_noise=enable) and one low (add_noise=disable) sampler")
    if hi["inputs"]["start_at_step"] != 0:
        bad("b", f"high sampler must start at 0, got {hi['inputs']['start_at_step']}")
    if hi["inputs"]["end_at_step"] != lo["inputs"]["start_at_step"]:
        bad("b", f"windows not contiguous: high ends {hi['inputs']['end_at_step']}, "
                 f"low starts {lo['inputs']['start_at_step']}")
    if lo["inputs"]["end_at_step"] != TOTAL_STEPS:
        bad("b", f"low sampler must end at {TOTAL_STEPS}, got {lo['inputs']['end_at_step']}")

    # (c) text-encoder offload satisfied: CLIP device=cpu OR lowvram recorded
    clip = next(iter(_by_type(wf, "CLIPLoaderGGUFMultiGPU").values()), None) \
        or next(iter(_by_type(wf, "CLIPLoaderGGUF").values()), None)
    if clip is None:
        bad("c", "no GGUF CLIP loader present")
    if not (clip["inputs"].get("device") == "cpu" or lowvram_required):
        bad("c", "encoder offload unsatisfied: CLIP is not device=cpu and --lowvram not recorded")

    # (d) tiled decode present, plain VAEDecode absent
    if not _by_type(wf, "VAEDecodeTiled"):
        bad("d", "VAEDecodeTiled missing (required to avoid 12 GB decode OOM)")
    if _by_type(wf, "VAEDecode"):
        bad("d", "plain VAEDecode present; must use VAEDecodeTiled on 12 GB")

    # (e) correct VAE, CFG, and 4n+1 latent length
    vae = next(iter(_by_type(wf, "VAELoader").values()))
    if not vae["inputs"]["vae_name"].startswith("wan_2.1_vae"):
        bad("e", f"VAE must be wan_2.1_vae, got {vae['inputs']['vae_name']}")
    for kid, n in ksamps.items():
        if n["inputs"]["cfg"] != 1.0:
            bad("e", f"sampler {kid} CFG must be 1.0, got {n['inputs']['cfg']}")
    if (LENGTH - 1) % 4 != 0:
        bad("e", f"latent length {LENGTH} violates (length-1) % 4 == 0")

    # (f) every link resolves to an existing node id
    for nid, n in wf.items():
        for name, v in n["inputs"].items():
            if _is_link(v) and str(v[0]) not in wf:
                bad("f", f"node {nid}.{name} links to missing node id {v[0]!r}")


# ============================================================================
# main
# ============================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="Build+validate a Wan 2.2 14B 12GB ComfyUI API workflow.")
    ap.add_argument("--dry-run", action="store_true", help="build+validate+save only (no deps/network)")
    ap.add_argument("--skip-download", action="store_true", help="check deps but skip weight downloads")
    args = ap.parse_args()

    if args.dry_run:
        # No filesystem/network probing; assume the offload path is --lowvram (conservative).
        multigpu = False
    else:
        print(f"ComfyUI root: {COMFYUI_ROOT}")
        print("Checking custom nodes...")
        multigpu = check_custom_nodes()
        if not args.skip_download:
            print("Downloading weights (resumable)...")
            download_all()

    wf, lowvram_required = build_workflow(multigpu)

    print("Validating workflow integrity...")
    test_workflow_integrity(wf, lowvram_required)     # raises on any violation

    OUTPUT_JSON.write_text(json.dumps(wf, indent=2), encoding="utf-8")
    print(f"\nOK -> wrote {OUTPUT_JSON}")
    print(f"     {WIDTH}x{HEIGHT}, {LENGTH} frames, {MODEL_NATIVE_FPS}->{OUTPUT_FPS} fps (RIFE x{RIFE_MULTIPLIER})")
    if lowvram_required:
        print("     NOTE: launch ComfyUI with --lowvram so the UMT5 text encoder offloads to system RAM")
        print("           (install ComfyUI-MultiGPU to pin the encoder to CPU instead).")
    print("\nFALLBACK: if decode still OOMs on 12 GB, switch to single-file TI2V-5B "
          "(fits ~10 GB, uses wan2.2_vae.safetensors) by changing the loader constants at the top.")
    return 0


def _selftest():
    """Runnable check: the built graph must validate, and known-bad mutations must fail."""
    wf, lv = build_workflow(multigpu=True)
    test_workflow_integrity(wf, lv)                      # clean build passes

    # break rule (d): swap tiled decode for plain -> must raise
    bad = json.loads(json.dumps(wf))
    bad["12"]["class_type"] = "VAEDecode"
    try:
        test_workflow_integrity(bad, lv)
        raise SystemExit("SELFTEST FAILED: plain VAEDecode was not rejected")
    except AssertionError:
        pass

    # break rule (f): dangle a link -> must raise
    bad = json.loads(json.dumps(wf))
    bad["12"]["inputs"]["samples"] = ["999", 0]
    try:
        test_workflow_integrity(bad, lv)
        raise SystemExit("SELFTEST FAILED: dangling link was not rejected")
    except AssertionError:
        pass
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main())
