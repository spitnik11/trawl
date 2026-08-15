# ComfyUI Generation Backend — Portable Reference Implementation

> A **framework-neutral specification** for driving a local ComfyUI install to run parameterized
> image and video generations, extracted from the **Z-Image Studio** architecture
> (`github.com/spitnik11/z-image-studio`, `server/src/{comfy,workflow,video,index}.ts`). It is the
> canonical design; Trawl's `core/render.py` is a compact stdlib port of the *still-image* slice.
>
> This document is decoupled from any one app or language. The reference code is illustrative
> (Python stdlib + language-neutral pseudocode) so the backend can be reimplemented anywhere.
> Nothing here depends on React/Express/TypeScript — those are Z-Image Studio's shell, not the backend.
>
> Last updated 2026-08-07. Source of truth for the live implementation: the Z-Image Studio repo.

---

## 0. What "the backend" is (and is not)

The generation backend is a **thin, stateless translator** between a typed generation request and a
running ComfyUI server. It does **not** run inference, own models, or manage GPU memory — ComfyUI
does all of that. The backend's entire job is four things:

1. **Validate** a generation request into a safe, bounded parameter set.
2. **Compile** those parameters into a ComfyUI *API-format* graph (nodes + wired inputs).
3. **Submit** the graph and **monitor** it to a terminal state without ever losing or inventing a result.
4. **Retrieve + persist** the output and its metadata.

Everything else (UI, prompt engineering, LoRA catalogs, training) sits *above* this layer. Keeping the
backend this small is the design: stock ComfyUI already owns memory management (async/pinned offload),
so the backend builds **no custom nodes** and never calls `free_memory`/`gc` manually.

```
   typed request ──▶ [validate] ──▶ [build graph] ──▶ [submit] ──▶ [monitor: WS + poll] ──▶ [retrieve] ──▶ output + metadata.json
                        schema         per-arch          /prompt      dual reconcile           /view          (write-once)
```

---

## 1. Component map

| Component | Responsibility | Z-Image Studio file |
|---|---|---|
| **Client** | HTTP + WebSocket wrapper over ComfyUI's API. No app logic. | `comfy.ts` |
| **Request schema** | One validated, bounded shape for every generation. Trust boundary. | `workflow.ts` (`generationSchema`, `videoGenerationSchema`) |
| **Architecture router** | Map a model filename → architecture family → the right graph builder. | `workflow.ts` (`modelArchitecture`) |
| **Graph builders** | Turn a validated request into an API-format node graph, per family. | `workflow.ts`, `video.ts` |
| **Orchestrator** | Submit → dual monitor → reconcile → finish → persist. Owns robustness. | `index.ts` (`monitor`, `monitorHistory`, `finishGeneration`) |

The dependency direction is strict: **Orchestrator → Builders → Client**. Builders are pure functions
(request in, graph out — no I/O). The client is dumb transport. All the hard-won robustness lives in
the orchestrator.

---

## 2. The ComfyUI API surface (all you need)

ComfyUI exposes a small HTTP + WS API. The backend uses exactly these endpoints:

| Call | Method | Purpose |
|---|---|---|
| `/object_info` | GET | Live catalog of installed nodes/models. Used to validate names and detect optional nodes. **Expensive** — cache it. |
| `/prompt` | POST | Submit a graph: `{prompt: <graph>, client_id, [front|number]}` → `{prompt_id}`. |
| `/history/<id>` | GET | Terminal record of a finished prompt, including output filenames. |
| `/queue` | GET / POST | Inspect the queue; `POST {delete:[id]}` removes a *queued* (not running) prompt. |
| `/interrupt` | POST | Stop the **currently running** prompt immediately. |
| `/view?filename=&subfolder=&type=` | GET | Fetch a produced image/video by its history-reported name. |
| `/ws?clientId=<id>` | WS | Live events for *your* client: `progress`, `executing`, `execution_error`. |

**Graph format.** Submit the **API format** (a flat `{ "<nodeId>": {class_type, inputs, _meta?} }`
dict), *not* the UI graph (which carries node positions). Inputs are literals or links `["<nodeId>", <outputIndex>]`.

### 2.1 Minimal client (Python stdlib — portable, zero deps)

```python
import json, urllib.request, urllib.parse, uuid

class Comfy:
    def __init__(self, url="http://127.0.0.1:8188"):
        self.url = url.rstrip("/")

    def _get(self, path, timeout=10):
        with urllib.request.urlopen(self.url + path, timeout=timeout) as r:
            return json.load(r)

    def _post(self, path, obj, timeout=60):
        req = urllib.request.Request(self.url + path,
                                     data=json.dumps(obj).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    def object_info(self):            return self._get("/object_info", timeout=60)
    def history(self, pid):           return self._get(f"/history/{pid}")
    def queue(self):                  return self._get("/queue")
    def interrupt(self):              return self._post("/interrupt", {})
    def submit(self, graph, client_id, priority="normal"):
        body = {"prompt": graph, "client_id": client_id}
        if priority == "next": body["front"] = True
        if priority == "low":  body["number"] = 1_000_000_000_000  # deferred
        return self._post("/prompt", body)["prompt_id"]
    def view_url(self, filename, subfolder="", type="output"):
        q = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": type})
        return f"{self.url}/view?{q}"
```

> **`/object_info` caching is mandatory at scale.** A large local library makes it multi-second.
> Z-Image Studio dedupes concurrent startup reads and reuses the result for ~5s so a batch of
> submissions doesn't re-fetch it N times. Cache with a short TTL; drop the cache on error.

---

## 3. The request schema (the trust boundary)

Every generation is one validated object. **Validation is a security control, not a convenience** —
it is the boundary between the (possibly hostile) caller and a process that reads local files and
writes to disk. Reject out of range; never clamp silently past a safety limit.

### 3.1 Image request (canonical fields)

| Field | Bound | Default | Notes |
|---|---|---|---|
| `prompt` | 1–4000 chars | — | required |
| `negativePrompt` | ≤2000 | `""` | empty ⇒ zeroed negative conditioning (see §5) |
| `width`,`height` | 256–2048 int | — | exact output size; large ⇒ tiled decode (§6) |
| `seed` | ≥0 int | — | `0` may mean "randomize at submit" |
| `steps` | 1–60 | 8 | turbo/lightning families want few steps |
| `guidance` (CFG) | 0–10 | 1 | turbo families are CFG-1 |
| `batchSize` | 1–4 | 1 | |
| `sampler` | enum | `res_multistep` | family may override |
| `scheduler` | enum | `simple` | |
| `outputFormat` | `png`\|`webp` | `png` | webp ⇒ animated (batch = frames) |
| `outputName` | `^[\w-]+(/[\w-]+)*$` | `z-image` | path-safe prefix only |
| `diffusionModel`,`textEncoder`,`vae` | non-empty | — | filenames validated against live loaders |
| `loras[]` | ≤8, strength −2..2 | `[]` | name must not be absolute / contain `..` |
| `references[]` | ≤4, mode `pose`\|`direct`\|`face`, strength 0..2 | `[]` | path-restricted |
| `initImage` | path-safe, optional | — | present + `img2imgStrength<1` ⇒ img2img (§7) |
| `img2imgStrength` | 0.05–1 | 1 | 1 = pure txt2img |
| `neuralUpscale`,`upscaleModel` | bool / path-safe | false / `RealESRGAN_x4plus.pth` | post-decode detail pass |
| `faceRefinement` | bool | false | Impact-Pack FaceDetailer after decode |

**Path fields must reject** absolute paths and `..`, and match a strict allowlist regex. This is
non-negotiable — these values become `LoadImage`/`SaveImage`/loader filenames.

### 3.2 Video request (SCAIL-2) extra constraints

`width`,`height` **divisible by 32**; `frameCount` ∈ {9,13,17,…,81} (i.e. `(n-1) % 4 == 0`);
`fps` 4–30; a `referenceImage` + `drivingVideo` (both upload-name-safe); manual mode requires
both `referenceMask` and `drivingMask`.

---

## 4. Architecture routing

One installed diffusion filename determines the **entire graph shape**. Route first, then build.

```python
import re
def model_architecture(filename: str) -> str:
    f = filename.lower()
    if "illustrious" in f or re.search(r"autismmix.*sdxl", f): return "illustrious"
    if re.search(r"krea[\s_.-]*2", f):                          return "krea2"
    if re.search(r"z[\s_.-]*image", f):                         return "z-image"
    if "anima" in f and not re.search(r"qwen_3_06b|qwen_image_vae|text.?encoder", f): return "anima"
    return "unknown"   # reject: never guess a graph for an unknown model
```

| Family | Loader | Text encoder | VAE | Latent | Sampler defaults | LoRA node | References |
|---|---|---|---|---|---|---|---|
| **Z-Image** | UNET split | Qwen3-4B `CLIPLoader lumina2` | Flux AE | SD3 + AuraFlow shift 3 | `res_multistep`/`simple`, CFG 1, 8 steps | `LoraLoaderModelOnly` | Z-Image ControlNet-Union (SDPose / Canny) |
| **Krea 2** | UNET split | Qwen3-VL `CLIPLoader krea2` | Qwen Image VAE | `EmptyLatentImage` (no AuraFlow) | `euler`/`simple`, CFG 1, 8 steps | `LoraLoaderModelOnly` | Identity Edit (direct) + Depth LoRA (pose) |
| **Illustrious XL** | `CheckpointLoaderSimple` | baked CLIP, **Clip Skip 2** | baked | `EmptyLatentImage` (SDXL) | user sampler, SDXL | `LoraLoader` (model+clip) | SDXL OpenPose / Canny ControlNet |
| **Anima** | `UNETLoader` | Qwen3-0.6B (`CLIPLoader stable_diffusion`, auto-detected) | Qwen Image VAE | `EmptyLatentImage` | `euler`/`simple`, CFG ~4, ~30 steps | `LoraLoaderModelOnly` | Anima LLLite pose / lineart patches |

**Rule:** an `unknown` architecture is a hard reject, not a fallback. Guessing a graph for an
unrecognized model produces silent garbage.

---

## 5. The canonical still graph (Z-Image reference)

This is the reference wiring every image family varies from. Node IDs are stable so overlays
(LoRA, references, img2img, face polish, upscale) can attach deterministically.

```
[1] UNETLoader ─┐
                ├─(LoRA chain 20,21,…)─▶ model ─┐
[2] CLIPLoader(lumina2) ─▶ [4] CLIPTextEncode(+) ┤
                          ▶ [12] CLIPTextEncode(−, optional) │
[3] VAELoader ────────────────────────────────────────────┐ │
[5] ConditioningZeroOut (used as neg when negative empty)  │ │
[6] EmptySD3LatentImage (w,h,batch) ──────────────┐        │ │
[7] ModelSamplingAuraFlow (shift 3) ◀─model        │        │ │
[8] KSampler(seed,steps,cfg,sampler,sched) ◀───────┴─pos,neg,latent
                                          │
[9] VAEDecode  (→ VAEDecodeTiled if big)  ◀─ samples,[3]
[11] ImageScale (lanczos, exact w×h)      ◀─ [9]
[10] SaveImage | SaveAnimatedWEBP         ◀─ [11]
```

**Two subtleties that are easy to get wrong:**

- **Negative conditioning.** For CFG-1 turbo families an empty negative uses a `ConditioningZeroOut`
  (`[5]`) rather than an empty text encode. Only wire a second `CLIPTextEncode` (`[12]`) when the
  user actually supplies negative text: `neg = negativePrompt ? ["12",0] : ["5",0]`.
- **Exact-size Lanczos (`[11]`).** Latent dimensions floor to multiples of the model's block size
  (e.g. a 1350px request floors to 1344). Generate at the floored latent size, then **scale to the
  exact requested pixels after decode** with a Lanczos `ImageScale`. This is why `[11]` always exists.

### 5.1 Deterministic node-ID ranges (overlay convention)

The builder reserves ID ranges so optional passes never collide:

| Range | Owns |
|---|---|
| `1`–`12` | core loaders, encode, latent, sampler, decode, scale, save |
| `20+` | LoRA chain (one node per LoRA, threaded model→model) |
| `40`–`41` | img2img source: `LoadImage` → `ImageScale` |
| `43`–`47`, `90`–`91` | structure-lock control (Canny/depth → ControlNet/LLLite) |
| `100 + i*10` | reference *i* sub-chain (load → fit → preprocess → apply) |
| `160+`,`180+` | Krea direct/pose reference chains |
| `189`–`190` | face detect + `FaceDetailer` |
| `300`–`301` | neural upscale (`UpscaleModelLoader` → `ImageUpscaleWithModel`) |

### 5.2 Building from a template vs from scratch

Two valid strategies (Z-Image Studio uses both):

- **Template patch** (Z-Image/Krea): start from a verified exported `*-api.json`, `structuredClone`
  it, and overwrite only the inputs that change (model names, prompt text, seed, size, sampler). The
  shared template file is never edited on disk — overrides are applied to the clone at submit time.
  *This is what Trawl's `render.py` does.* Safest when a known-good graph exists.
- **Programmatic build** (Illustrious/Anima/SCAIL): construct the dict node-by-node in code. Needed
  when node topology varies with inputs (references, masks).

```python
# Template-patch strategy (mirrors render.py / buildWorkflow)
import copy, json, random
def build_still(template: dict, p: dict, unet, clip, vae) -> dict:
    g = copy.deepcopy(template)
    g["1"]["inputs"]["unet_name"] = unet
    g["2"]["inputs"]["clip_name"] = clip
    g["3"]["inputs"]["vae_name"]  = vae
    g["4"]["inputs"]["text"] = p["prompt"]
    g["8"]["inputs"]["negative"] = ["12", 0] if p.get("negativePrompt") else ["5", 0]
    if p.get("negativePrompt"): g["12"]["inputs"]["text"] = p["negativePrompt"]
    g["6"]["inputs"].update(width=p["latentW"], height=p["latentH"], batch_size=p.get("batch", 1))
    g["8"]["inputs"].update(seed=p.get("seed") or random.randint(1, 2**31-1),
                            steps=p.get("steps", 8), cfg=p.get("cfg", 1))
    g["11"]["inputs"].update(width=p["width"], height=p["height"])   # exact output
    # LoRA chain: thread model through 20,21,…
    src = ["1", 0]
    for i, lora in enumerate(p.get("loras", [])):
        nid = str(20 + i)
        g[nid] = {"class_type": "LoraLoaderModelOnly",
                  "inputs": {"model": src, "lora_name": lora["name"], "strength_model": lora["strength"]}}
        src = [nid, 0]
    g["7"]["inputs"]["model"] = src   # AuraFlow patch takes the end of the LoRA chain
    return g
```

---

## 6. Tiled VAE and exact scaling (VRAM safety)

Full-frame VAE decode OOMs on 12 GB at large canvases. Switch to tiled decode above a pixel
threshold; the post-decode Lanczos scale then restores exact size.

```python
TILED_VAE_PIXEL_THRESHOLD = 1080 * 1920
def configure_vae_decode(node: dict, width: int, height: int):
    if width * height < TILED_VAE_PIXEL_THRESHOLD:
        return
    node["class_type"] = "VAEDecodeTiled"
    node["inputs"].update(tile_size=512, overlap=64, temporal_size=64, temporal_overlap=8)
```

Same mechanism guards the near-720p SCAIL video path (which *always* uses tiled decode on 12 GB).

---

## 7. Optional passes (compose onto the core graph)

Each is a pure mutation of the graph dict, applied only when requested. Applied **after** the core
graph exists, in this order: **img2img latent → structure lock → face polish → neural upscale**.

- **img2img / "Improve"** (`initImage` + `strength < 1`): replace `EmptyLatentImage` `[6]` with
  `LoadImage[40] → ImageScale[41] → VAEEncode[6]`, and set the sampler's `denoise` from strength.
  Two modes: *refine* (hard-cap denoise ≤ ~0.38, keep pose/detail) and *rewrite* (freer). A light
  noise term blends in extra freedom.
- **Structure lock** (keep layout while refining): re-feed the fitted init canvas as a control signal,
  architecture-specific — Z-Image ControlNet-Union (Canny), Illustrious SDXL Canny ControlNet, Anima
  lineart LLLite, Krea depth control LoRA.
- **References** (pose/direct/face): per family, chained off node `100 + i*10`. Pose ⇒ keypoints
  (SDPose/OpenPose); direct/face ⇒ Canny or identity edit. Identity is *approximate* except where a
  native identity path exists (Krea Identity Edit). Standard SD/SDXL IP-Adapter weights are **not**
  compatible with Z-Image/Krea — don't reach for them.
- **Face polish** (`faceRefinement`): `UltralyticsDetectorProvider[189]` + `FaceDetailer[190]` at
  low denoise (~0.4) after decode. Saves a single final image (no side-by-side branch).
- **Neural upscale** (`neuralUpscale`): `UpscaleModelLoader[300] → ImageUpscaleWithModel[301]`
  inserted before the exact-size scale. Architecture-neutral (Real-ESRGAN / UltraSharp / Remacri).

---

## 8. The SCAIL-2 video graph (reference)

```
[1] LoadImage(reference) ─────────────┐
[2] LoadVideo(driving) → [3] GetVideoComponents → [4] ImageFromBatch(frameCount) → [5] ImageScale(w,h)
[6] UNETLoader(SCAIL mxfp8)   [7] CLIPLoader(umt5 'wan')   [8] VAELoader(Wan 2.1)   [9] CLIPVisionLoader
[10] CLIPTextEncode(+)   [11] CLIPTextEncode(−, empty)   [12] CLIPVisionEncode([1])
   masks:  auto → SAM3_VideoTrack(driving)+SAM3(ref) → SCAIL2ColoredMask
           manual → LoadImage/LoadVideo mask + ImageScale(nearest-exact)
[18] WanSCAILToVideo(pos,neg,vae,w,h,length,pose_strength/start/end, pose_video[5], masks, ref[1], clipvis[12])
[19] ModelSamplingSD3(shift 5) ◀ [6]
[20] KSampler(euler/simple, cfg, denoise 1) ◀ model[19], pos/neg/latent from [18]
[21] VAEDecode → [22] CreateVideo(fps, audio from [3]) → [23] SaveVideo(h264 mp4)
```

Invariants: **dims ÷32**, **frameCount 4n+1 ∈ [9,81]**, **Wan model-sampling shift 5**,
**euler/simple**, one active video job at a time, `previous_frame_count 5` for continuity.
`animation` vs `replacement` flips `replacement_mode` on the mask + video nodes.

---

## 9. Orchestration — submit, monitor, reconcile, persist

This is where correctness is won or lost. The naive "submit then poll history" loses cached results
and hangs on ghosts. The reference orchestrator runs **two monitors in parallel** and fails safe.

### 9.1 The dual-monitor pattern (load-bearing)

```
submit(graph, clientId, priority) → promptId
   ├─ WebSocket(clientId):   progress → update %; executing(node=null) → done → reconcile via history;
   │                          execution_error → fail
   └─ history poll (every 2s): terminal in history → finish;
                               empty history AND not in queue after grace → orphan → fail (never invent)
whichever fires first wins; the other is stopped.
```

Why both:

- **WebSocket alone loses the cached-completion race.** If ComfyUI has an identical graph cached, it
  can finish *before* your socket attaches — you get no `executing` event and wait forever. The 2s
  history poll catches this (the job is already terminal in history).
- **History poll alone is laggy and can't stream progress.** The socket gives live `progress`
  (`value/max`) and immediate `execution_error`.
- **Ghost/orphan detection.** If history is empty *and* the prompt is not in the queue after a grace
  period, the job is gone — mark it **failed with an explicit message**. Never fabricate an output.
  Only recover a "completed" state when there is saved media **plus** progress/duration evidence.

```python
import time
def run(comfy: "Comfy", graph: dict, on_progress=None, timeout_s=240, grace_s=90):
    client_id = uuid.uuid4().hex
    pid = comfy.submit(graph, client_id)
    deadline = time.monotonic() + timeout_s
    first_seen = None
    while time.monotonic() < deadline:
        try:
            hist = comfy.history(pid)
        except Exception:
            time.sleep(2); continue                 # Comfy briefly unreachable ≠ failure
        if pid in hist:                             # terminal: outputs are recorded
            return _outputs(hist[pid])              # {node: {images|gifs|...}}
        in_queue = _prompt_in_queue(comfy.queue(), pid)
        if not in_queue:
            first_seen = first_seen or time.monotonic()
            if time.monotonic() - first_seen > grace_s:
                raise RuntimeError(f"ghost job {pid}: gone from history and queue")  # fail safe
        else:
            first_seen = None
        time.sleep(2)
    comfy.interrupt()                                # best-effort cancel on timeout
    raise TimeoutError(f"render timed out after {timeout_s}s (prompt {pid})")
```

> The snippet above is the **poll-only** reconciler (what Trawl's `render.py` uses — no ws dep). The
> full Z-Image Studio orchestrator adds the WebSocket for live progress and instant error surfacing;
> the poll loop remains as the safety net. If you only need correctness, poll-only is enough; add the
> socket for UX.

### 9.2 Retrieve + persist

Read output filenames from the terminal history (`outputs[<saveNodeId>].images|gifs`), fetch bytes via
`/view`, write to a controlled output folder, and write a **write-once** metadata sidecar
(`{id}.json`, open with exclusive/`wx` flag) recording the request, model stack, versions, and
resolved seed. Deleting a gallery record must never delete the original ComfyUI file.

---

## 10. Priority, cancel, batch

- **Priority → queue placement.** `next` ⇒ `{front:true}`; `low` ⇒ a large `number` (deferred);
  `normal` ⇒ default. This is ComfyUI's native ordering — don't build a scheduler.
- **Cancel.** `/interrupt` stops the *running* prompt; `/queue {delete:[id]}` removes a *queued* one.
  A single global interrupt is the only handle on the running job — design around that limit.
- **Batch.** `batchSize` in the latent for a single graph (up to 4), or submit N graphs with distinct
  seeds. For visible variety, **distinct seeds beat one big batch**, and over-stacking LoRAs (identity/
  style adapters dominating) collapses output variety regardless of seed — a real, diagnosed failure
  mode, not a bug.

---

## 11. Safety boundaries (carry all of these)

- **Bind localhost only.** Backend and ComfyUI both on `127.0.0.1`. No public binding, no accounts.
- **The browser never supplies a raw workflow.** Graphs are built server-side from the validated
  schema. Client-supplied JSON is data to validate, never a graph to run.
- **Validate names against live loaders.** Model/LoRA/VAE/upscale filenames are checked against the
  live `/object_info` loader lists; unknown filenames are disabled/rejected.
- **Path containment.** Every path field rejects absolute paths and `..` and matches a strict regex.
  Output paths are resolved and asserted to stay inside the configured root.
- **Uploaded media** is MIME/decode-checked, internally renamed, dropped into a controlled ComfyUI
  input folder, and never executed. Decode (e.g. FFprobe) before use.
- **Never overwrite on save** (exclusive-create the metadata file). Library uploads 409 on an
  existing filename rather than clobbering.
- **Fail safe, never fabricate.** An unreachable service, empty history, or ghost job resolves to an
  explicit failure — never a synthesized "success."

---

## 12. Anti-patterns (explicitly rejected by this architecture)

These were considered and rejected in Z-Image Studio; a port should reject them too:

- **Building custom ComfyUI nodes / a custom execution router.** The backend drives *stock* ComfyUI
  via its API. Custom nodes couple you to a fork and break on upgrade.
- **Manual memory management** (`free_memory`, `unload_all_models`, `gc.collect`, forcing fp16).
  ComfyUI owns memory (async/pinned offload). Manual calls fight it and cause stalls.
- **Editing the shared workflow template on disk** to change a generation. Clone and override inputs
  on the copy; the template stays a verified constant.
- **Trusting a single completion signal.** Always reconcile (WS + history), always have a ghost path.

---

## 13. How to port this (checklist)

1. Implement the **client** (§2.1) in your language — 7 methods over HTTP, one WS.
2. Define the **request schema** (§3) with hard bounds and path allowlists. This is the trust boundary.
3. Implement `model_architecture()` (§4) and one **builder per family** you support (start with one).
4. Reserve the **node-ID ranges** (§5.1) so optional passes compose without collision.
5. Implement the **dual monitor** (§9.1) — or poll-only first, add WS for progress later.
6. Add **tiled-VAE** (§6) and **exact-scale** the moment you support large canvases.
7. Enforce every **safety boundary** (§11) before exposing the backend to anything but yourself.
8. Persist output + **write-once metadata**; never delete source files on record deletion.

The smallest correct backend is: client + one builder (template-patch) + poll-only monitor +
path validation. That is exactly `trawl/core/render.py`. Everything else in this document is the
road from that minimum to the full Z-Image Studio backend.
