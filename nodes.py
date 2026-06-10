"""
LlamaCPP Ideogram Prompt Builder — ComfyUI Custom Nodes
--------------------------------------------------------
Nodes:
  1. LlamaCppIdeogramPrompter  — dropdown of live models, calls your llama.cpp
     server, strips <think> blocks, returns clean Ideogram JSON. Has a built-in
     "unload after generation" toggle so you don't need a separate node.

  2. LlamaCppJsonViewer  — takes any STRING and pretty-prints it as formatted
     JSON for easy inspection. Wire the ideogram_json output here to see the
     full structured output in the UI.
"""

import json
import re
import urllib.request
import urllib.error
import time

# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_URL = "http://127.0.0.1:8082"


def _post_json(url: str, payload: dict, timeout: int = 300) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url: str, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_model_list(server_url: str = _DEFAULT_URL) -> list:
    """
    Hits /v1/models and returns model IDs as a list for the dropdown.
    Falls back gracefully if the server is offline at ComfyUI startup.
    """
    try:
        resp = _get_json(f"{server_url.rstrip('/')}/v1/models", timeout=5)
        ids  = [m["id"] for m in resp.get("data", []) if m.get("id")]
        if ids:
            print(f"[LlamaCPP] Found {len(ids)} models on {server_url}")
            return ids
    except Exception as exc:
        print(f"[LlamaCPP] Could not fetch model list from {server_url}: {exc}")
    return ["(server offline — reload page when server is running)"]


def _unload_all_models(base: str, wait_seconds: int = 3) -> bool:
    """
    Matches the working Open WebUI VRAM unload tool exactly:
      GET  /v1/models        -> list all models, find those with status "loaded"
                                status can be plain string OR {"value": "loaded"}
      POST /models/unload    -> {"model": "<id>"} for each loaded one
    """
    base = base.rstrip("/")
    unloaded_count = 0
    failed_count = 0

    # Step 1: fetch via /v1/models (same as the working tool)
    try:
        resp = _get_json(f"{base}/v1/models", timeout=10)
        all_models = resp.get("data", [])
    except Exception as exc:
        print(f"[LlamaCPP VRAM] Could not reach /v1/models: {exc}")
        return False

    # Step 2: filter loaded — status is either "loaded" or {"value": "loaded"}
    loaded_ids = []
    for m in all_models:
        model_id = m.get("id", "")
        if not model_id:
            continue
        status = m.get("status", {})
        status_val = status.get("value", "") if isinstance(status, dict) else status
        if status_val == "loaded":
            loaded_ids.append(model_id)

    if not loaded_ids:
        print("[LlamaCPP VRAM] No models currently loaded — nothing to unload.")
        return True

    print(f"[LlamaCPP VRAM] Found {len(loaded_ids)} loaded: {loaded_ids}")

    # Step 3: POST /models/unload per model
    for model_id in loaded_ids:
        print(f"[LlamaCPP VRAM] Unloading '{model_id}'...")
        try:
            result = _post_json(f"{base}/models/unload", {"model": model_id}, timeout=30)
            print(f"[LlamaCPP VRAM] Unloaded '{model_id}' -> {result}")
            unloaded_count += 1
        except Exception as e:
            print(f"[LlamaCPP VRAM] Failed to unload '{model_id}': {e}")
            failed_count += 1

    print(f"[LlamaCPP VRAM] Done — unloaded {unloaded_count}, failed {failed_count}.")

    if unloaded_count > 0 and wait_seconds > 0:
        print(f"[LlamaCPP VRAM] Waiting {wait_seconds}s for VRAM reclaim...")
        time.sleep(wait_seconds)

    return unloaded_count > 0


def _strip_thinking(text: str) -> str:
    """
    Remove <think>...</think> blocks that reasoning/thinking models emit
    before their actual answer. Handles multiline, greedy-safe.
    """
    # Remove <think> ... </think> blocks (case-insensitive, dotall)
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Also handle models that use a bare chain-of-thought prefix like "Thinking:\n..."
    # followed by the actual JSON (heuristic: strip everything before the first '{')
    cleaned = cleaned.strip()
    if cleaned and cleaned[0] != "{":
        brace = cleaned.find("{")
        if brace != -1:
            cleaned = cleaned[brace:]
    return cleaned.strip()


# ──────────────────────────────────────────────────────────────────────────────
# Built-in Ideogram system prompt
# ──────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = r"""You convert a natural-language user idea into a structured JSON caption an image renderer can consume. You receive the user idea plus a target aspect ratio, and you emit one JSON object.

## STEP 0 — CREATIVE INTERPRETATION (run this first, silently, before building the JSON)

You are not just a transcriber — you are a creative director with full imaginative license. Before laying out any elements, ask yourself:

**What is the user REALLY asking for?**

Many prompts are open-ended, fictional, meme-flavoured, pop-culture-referential, or intentionally absurd. Your job is to commit to the most interesting, specific, visually compelling interpretation — then execute it with total confidence. Examples of how to think:

- "a banned episode of SpongeBob" → Pick a SPECIFIC real banned or controversial episode (e.g. the 'Rock-a-Bye Bivalve' episode, or 'Mid-Life Crustacean') and render it as a faithful animated screenshot: the show's iconic flat Nickelodeon animation style, widescreen 16:9 frame, accurate character designs, Bikini Bottom underwater setting with appropriate background art, character action/dialogue matching that episode's tone. NOT a film-noir fever dream — a CARTOON SCREENSHOT. If you don't know a specific banned episode, invent a plausible one and commit to it fully.
- "a fake movie poster for my cat" → Full Hollywood poster treatment, invented tagline, your cat as the lead.
- "a meme template" → Recognize the specific meme format and recreate it faithfully.
- "what if X met Y" → Crossover art, commit to a specific encounter scene.
- "a tweet / text message / DM from [character]" → UI screenshot of that platform, in-character message.
- "a Wikipedia article about a fake thing" → Render the actual Wikipedia layout.
- "a magazine cover featuring [subject]" → Real magazine layout with masthead, cover lines, barcode.
- "a stock photo of [absurd concept]" → Render it in the flat, overlit, clip-art-adjacent style of iStock.

**Medium fidelity rule:** When the user references a specific existing visual medium (TV show screenshot, video game UI, OS interface, social media post, magazine, newspaper, book cover, trading card, ticket stub, receipt, ID card), render the AUTHENTIC medium format — correct aspect ratio, accurate UI chrome, correct font treatment, correct color palette for that property. A SpongeBob screenshot must look like SpongeBob, not like a noir film.

**Named IP fidelity:** When a specific character, franchise, show, game, or brand is named, use its ACTUAL visual identity. Commit to their canonical design, color palette, and art style. Never drift into a generic lookalike.

**Humor and absurdity are valid creative goals.** If the prompt is clearly comedic, lean into it. A funny image that lands is better than a technically correct image that misses the joke.

**When in doubt, be more specific, not less.** Invent a specific detail rather than leaving something vague. A vague prompt is an invitation to be creative, not an excuse to be generic.

---

## OUTPUT CONTRACT — exactly three top-level keys, in this order:

{"aspect_ratio":"W:H","high_level_description":"...","compositional_deconstruction":{"background":"...","elements":[ ... ]}}

- Emit a SINGLE-LINE MINIFIED JSON object — no markdown fences, no commentary, no other top-level keys.
- Preserve non-ASCII characters as-is (CJK, Cyrillic, Devanagari, Arabic, accented Latin). Never escape with \uNNNN, transliterate, or replace café with cafe.
- Use SINGLE quotes for embedded text references in prose fields ('Joe\'s Diner', not \"Joe's Diner\"). The text field of text elements is the exception — that field holds the user's verbatim characters and may use any characters.

### `aspect_ratio` (first field, always required)

A string in W:H form with positive integers (1:1, 16:9, 9:16, 4:5, 3:1, 2:3, etc.).
- If the user message gives a concrete W:H, echo it verbatim.
- If the user message says auto, pick a concrete ratio that matches the medium and composition. NEVER emit the literal string auto.
- The aspect ratio you commit to drives every bbox decision. Pick it first based on the target medium (TV screenshot → 16:9, phone screenshot → 9:16, poster → 2:3, etc.).

### `high_level_description` — observational summary (50-word hard cap)

- ONE long sentence preferred, never more than two.
- Reads like a short natural-language prompt, not an analysis. Starts immediately with the subject — no "this image shows", "depicts", "captures".
- Identifies subject(s), medium, and overall composition. Names recognized pop-culture entities by full name (Nike Air Jordan 1, Eiffel Tower, Mario (Nintendo character), SpongeBob SquarePants (Nickelodeon animated series)).
- For transparent backgrounds, include the literal phrase on a transparent background.

## ELEMENTS — what they are, what they're not

Each element is one of:
{"type":"obj","bbox":[y1,x1,y2,x2],"desc":"..."}
{"type":"text","bbox":[y1,x1,y2,x2],"text":"LINE ONE\nLINE TWO","desc":"..."}

bbox is optional per-element.

### SINGLE SUBJECT = SINGLE ELEMENT

A coherent subject — one animal, person, vehicle, building, plant, instrument, machine — is exactly ONE obj element. Anatomical and structural parts are descriptive attributes inside that element's desc, NOT separate elements.

FORBIDDEN: splitting a bee into thorax/abdomen/wings/eyes; splitting a car into body/wheels/windshield; splitting a person into head/torso/limbs; splitting a building into foundation/walls/windows/roof.

When MULTIPLE distinct subjects appear (a person AND a dog; two characters side by side), use MULTIPLE elements — one per subject.

### Element desc — what to write (30–60 words, 60-word HARD CAP)

Identity first, then major attributes briefly, then one distinguishing detail if relevant. Each desc is a standalone catalog entry — open with the subject's identity, not a referring phrase like "the X".

Major attributes — always name:
- People/characters: skin tone OR animation style/color palette, hair (color + style), each visible garment with color, expression/gaze, pose.
- Animated characters: canonical design details, art style, cel-shading approach.
- Objects: shape, material, color, distinctive parts.
- UI/screen elements: platform, style, color scheme, font treatment.

### Element desc — what NOT to include

No shadows (describe in background only). No camera/render language (depth of field, bokeh, motion blur) unless user explicitly named them — viewpoint/angle IS allowed. No impressionistic adjectives (luminous, vibrant, radiant) — use observable properties instead. No scene-context repetition per-element.

### Anchor placements to named references

Specify body parts, surfaces, spatial landmarks.
- CORRECT: applied to the forehead near the hairline above the left eyebrow.
- INCORRECT: pressed against the skin.

## BACKGROUND — what goes here, what doesn't

background describes the scene SHELL: walls and finishes, floor/ground surface, ceiling, architectural fixtures, sky/clouds, atmospheric context, scene-wide ambient lighting, distant out-of-focus context.

For animated/illustrated/UI screenshots: background describes the in-world background art (Bikini Bottom underwater coral reef background painted in the show's flat vector style, with the standard Nickelodeon teal water gradient and sandy floor).

### No double-counting

Anything described in background CANNOT also appear as an obj element. Each scene component lives in EXACTLY ONE field.

### ALWAYS-BACKGROUND — these live in background only, never as obj elements:

- sky, clouds, atmospheric color, horizon
- distant mountains, hills, tree lines, distant cityscape
- atmospheric weather (fog, haze, mist, smoke)
- the floor / ground / turf / paving surface
- ambient walls or studio backdrop behind focal subjects
- for UI screenshots: the platform chrome, status bar, app background

### Ground/floor/pavement is ALWAYS background — zero tolerance

The surface the scene sits on lives in background only. This holds regardless of how wet, reflective, or textured it is.

Discrete solid objects ON the floor are still elements: debris, rocks, dropped props remain obj elements. The rule applies to the SURFACE itself.

### Shell-affixed prominent objects → DUAL MENTION

Objects that are simultaneously part of the shell AND focal elements (a chalkboard covering the back wall, a large mounted TV, a fixed reception desk): 1) mention in background as part of the shell, 2) emit as an obj element with "the primary background element" qualifier at start of desc, 3) place first in elements list.

## BBOX STRATEGY

INCLUDE bboxes where precise positioning matters — portrait subjects, products, logos, signs, UI elements.
OMIT bboxes on dense/hard-to-enumerate visuals — crowds, fields of particles, starry skies.

### Coordinate system

Normalized 0–1000 in BOTH axes. x runs left→right along full width, y runs top→bottom along full height. Format [y1, x1, y2, x2] with y1 < y2, x1 < x2.

For round objects or square on-screen regions, scale spans so (x2-x1)/(y2-y1) ≈ W/H.

## SPECIFICITY — commit to one value

Leave nothing for the model to invent or choose.

Banned hedge phrasings: things like, such as, e.g., for example, or similar, various, could include, might be, some kind of, style of.
Banned alternative listings for one property: pale off-white or pale green, oak or walnut, late afternoon or early evening. Pick ONE and commit.
Banned implied/suggested hedges: implied, suggested, hinted, barely visible, possibly, perhaps, maybe, might be, reads as, almost.

Exhaustive content preservation: when the user provides enumerable content — schedules, lists, menu items, steps, names, times — every item must appear. Use as many text elements as needed.

Named prompt elements MUST appear: every explicitly-named visual unit in the user prompt must appear as its own element.

Don't invent visual concepts the user didn't ask for. If the prompt asks for a TV screenshot, render a TV screenshot — not a noir fever dream.

## PLANNING — turn the user idea into elements

### 1. Pick a medium

Decision: DESIGNED artifact vs CAPTURED / DRAWN / RENDERED moment.
- graphic design — poster, book cover, album cover, magazine cover, flyer, banner, sticker, logo, packaging, UI mockup, infographic, menu, greeting card, ticket, signage.
- photograph — portrait, landscape, lifestyle, street, sport, wildlife, food, product.
- illustration — cartoon, anime, manga, comic, watercolor, oil painting, ink, vector, pixel art, children's book, named studios (Ghibli, Pixar 2D, Nickelodeon-style).
- 3D render — CGI, octane/unreal/blender, hyperrealistic product render, isometric.
- screenshot / UI mockup — social media posts, text message threads, website screenshots, TV/film frame grabs, video game HUD captures. Use the AUTHENTIC platform visual design.

Silent / ambiguous → photograph (default). Imperative verbs like "Illustrate" or "Draw" are NOT medium signals.

### 2. Style commitment

Name the style ONCE in HLD/background prose (Studio Ghibli animation, Nickelodeon flat vector animation, 35mm film photograph, flat vector illustration). Keep it short — recognizable style names are enough.

### 3. Photoreal defaults — AVOID "warm"

For photographic prompts: default to iPhone aesthetic, ambient natural light, neutral white balance. The word "warm" as a grading adjective is BANNED — describe light sources concretely instead. Default to off-center, rule-of-thirds framing.

### 4. Populate underspecified scenes

Add believable secondary subjects, micro-props, environmental texture. Built environments need text everywhere — shops, stalls, vehicles carry text on practically every surface. Generate text generously: signs, labels, menus, price tags, posters.

Override: when the brief explicitly says minimal, empty, lonely, isolated, negative space, alone — respect the restraint.

## TEXT HANDLING

For each text element:
- text — literal characters appearing in the image, verbatim. Preserve diacritics, capitalization, punctuation.
- bbox — optional, same coordinate system as obj elements.
- desc — free-form prose covering size, location, font style, color, orientation, visual effects. For UI screenshots: match the platform's actual font (Whitney for Discord, SF Pro for iOS, Roboto for Android, etc.).

Sources of text to include:
1. User-quoted text — verbatim.
2. Format-required text — headlines, taglines, author names, dates, CTAs, brand names.
3. In-scene contextual text — signage, labels, badges, jersey numbers, t-shirt prints.
4. UI text — for screenshots: timestamps, usernames, notification text, UI labels.
5. Numeric content — race numbers, dates, prices, scores, time displays.

## POP CULTURE, BRANDS, NAMED REFERENCES

When the user idea names or clearly implies a brand, trademark, product, public figure, athlete, musician, actor, fictional character, film, show, game, franchise — the output MUST carry an explicit named reference in the relevant element desc. Don't replace Nike Dunk Low Panda with black and white retro sneakers, SpongeBob with a yellow sponge character — name the specific thing.

For animated/illustrated IP: commit to the canonical art style, color palette, and character designs of that property. SpongeBob renders in Nickelodeon's flat vector style, not as a photorealistic or cinematic interpretation.

## TRANSPARENT BACKGROUND

If the user's idea calls for transparent background, the background field MUST be exactly: transparent background

Emit ONLY the single-line minified JSON. No preamble, no markdown fences, no explanation."""


# ──────────────────────────────────────────────────────────────────────────────
# Node 1 — Ideogram Prompt Builder (combined with VRAM clear toggle)
# ──────────────────────────────────────────────────────────────────────────────

class LlamaCppIdeogramPrompter:
    """
    Calls your local llama.cpp server to expand a short user idea into the
    structured JSON that Ideogram-4 / CLIPTextEncode expects.

    Features:
    - Live model dropdown populated from /v1/models (refresh page to update)
    - Strips <think>...</think> blocks from reasoning/thinking models
    - Built-in "unload after generation" toggle — no separate node needed
    - Returns both the clean JSON and the full raw response for debugging
    """

    CATEGORY     = "LlamaCPP / Ideogram"
    FUNCTION     = "generate"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("ideogram_json", "raw_response")

    @classmethod
    def INPUT_TYPES(cls):
        models = _fetch_model_list(_DEFAULT_URL)
        return {
            "required": {
                "user_idea": ("STRING", {
                    "multiline": True,
                    "default": "A surreal streetwear collage poster with a skateboarder and giant puffy letters spelling COMFY",
                    "tooltip": "Short natural-language description of what you want to generate.",
                }),
                "aspect_ratio": ("STRING", {
                    "default": "9:16",
                    "tooltip": "Target aspect ratio passed to the LLM (e.g. 1:1, 16:9, 9:16, 4:5).",
                }),
                "model": (models, {
                    "tooltip": "Model to use. Reload the page to refresh this list from the server.",
                }),
                "server_url": ("STRING", {
                    "default": _DEFAULT_URL,
                    "tooltip": "Base URL of your llama.cpp server.",
                }),
                "temperature": ("FLOAT", {
                    "default": 0.6,
                    "min": 0.0,
                    "max": 2.0,
                    "step": 0.05,
                    "tooltip": "Sampling temperature. Lower = more focused JSON output.",
                }),
                "max_tokens": ("INT", {
                    "default": 8192,
                    "min": 256,
                    "max": 32768,
                    "step": 128,
                    "tooltip": "Max tokens including any thinking/reasoning tokens the model emits.",
                }),
                "unload_after": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Unload model after generation",
                    "label_off": "Keep model loaded",
                    "tooltip": "POST /models/unload to free VRAM before diffusion runs.",
                }),
                "unload_wait_seconds": ("INT", {
                    "default": 3,
                    "min": 0,
                    "max": 30,
                    "step": 1,
                    "tooltip": "Seconds to wait after unload for the GPU driver to reclaim VRAM.",
                }),
                "enable_thinking": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Thinking ON (deep reasoning)",
                    "label_off": "Thinking OFF (fast)",
                    "tooltip": "Passes thinking=true + budget_tokens to the API. Qwen3 / DeepSeek-R1 style models need this explicitly set or they skip the <think> block.",
                }),
                "thinking_budget": ("INT", {
                    "default": 4096,
                    "min": 512,
                    "max": 16384,
                    "step": 256,
                    "tooltip": "Max tokens the model may spend on internal reasoning (thinking budget). Only used when enable_thinking=True.",
                }),
                "always_rerun": ("BOOLEAN", {
                    "default": False,
                    "label_on": "Always rerun (new random JSON every queue)",
                    "label_off": "Use cache (skip LLM if prompt unchanged)",
                    "tooltip": (
                        "OFF (default): ComfyUI's normal caching applies — the LLM is skipped "
                        "when the prompt and settings haven't changed. "
                        "ON: forces the node to re-execute every queue run, giving a fresh "
                        "random JSON even when nothing has changed."
                    ),
                }),
            },
            "optional": {
                "system_prompt_override": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "Leave blank to use the built-in Ideogram system prompt.",
                }),
            },
        }

    @classmethod
    def IS_CHANGED(cls, always_rerun: bool = False, **kwargs):
        """
        When always_rerun is ON, return a random float so ComfyUI considers
        the node changed on every queue run and re-executes the LLM.
        When OFF, return a stable hash of the actual inputs so ComfyUI's
        normal caching kicks in and skips the LLM if nothing changed.
        """
        import hashlib, random
        if always_rerun:
            return random.random()
        # Stable hash from the inputs that actually affect the output
        key_parts = [
            str(kwargs.get("user_idea", "")),
            str(kwargs.get("aspect_ratio", "")),
            str(kwargs.get("model", "")),
            str(kwargs.get("temperature", "")),
            str(kwargs.get("max_tokens", "")),
            str(kwargs.get("enable_thinking", "")),
            str(kwargs.get("thinking_budget", "")),
            str(kwargs.get("system_prompt_override", "")),
        ]
        return hashlib.md5("|".join(key_parts).encode()).hexdigest()

    def generate(
        self,
        user_idea: str,
        aspect_ratio: str,
        model: str,
        server_url: str,
        temperature: float,
        max_tokens: int,
        unload_after: bool,
        unload_wait_seconds: int,
        enable_thinking: bool = True,
        thinking_budget: int = 4096,
        always_rerun: bool = False,
        system_prompt_override: str = "",
    ):
        server_url = server_url.rstrip("/")
        system     = system_prompt_override.strip() or _SYSTEM_PROMPT

        # Skip placeholder entry shown when server was offline at startup
        use_model = model.strip()
        if use_model.startswith("("):
            use_model = ""

        user_message = (
            f"TARGET IMAGE ASPECT RATIO: {aspect_ratio} (width:height).\n"
            f"User idea: {user_idea}"
        )

        payload: dict = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user_message},
            ],
            "max_tokens":  max_tokens,
            "temperature": temperature,
            "stream":      False,
        }
        if use_model:
            payload["model"] = use_model

        # ── Thinking / extended reasoning ─────────────────────────────────────
        # Qwen3 and similar models will skip <think> blocks unless the API
        # explicitly requests thinking mode. llama.cpp passes this through as
        # a top-level "thinking" object and also honours chat_format flags.
        if enable_thinking:
            # llama.cpp per-request thinking budget field (discussion #21445)
            payload["thinking_budget_tokens"] = thinking_budget
            print(
                f"[LlamaCppIdeogramPrompter] Thinking ENABLED  budget={thinking_budget} tokens"
            )
        else:
            # 0 disables thinking (equivalent to --reasoning-budget 0)
            payload["thinking_budget_tokens"] = 0
            print("[LlamaCppIdeogramPrompter] Thinking DISABLED")

        endpoint = f"{server_url}/v1/chat/completions"
        print(f"[LlamaCppIdeogramPrompter] POST {endpoint}  model={use_model or '(server default)'}")
        t0 = time.time()

        try:
            response = _post_json(endpoint, payload, timeout=600)
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"[LlamaCppIdeogramPrompter] Could not reach llama.cpp at {endpoint}.\n"
                f"Error: {exc}\nCheck server_url and that the server is running."
            ) from exc

        elapsed = time.time() - t0

        choices = response.get("choices", [])
        if not choices:
            raise RuntimeError(
                f"[LlamaCppIdeogramPrompter] Empty choices in response: {response}"
            )

        raw_content: str = choices[0].get("message", {}).get("content", "").strip()

        usage = response.get("usage", {})
        print(
            f"[LlamaCppIdeogramPrompter] Done in {elapsed:.1f}s  |  "
            f"prompt={usage.get('prompt_tokens','?')}  "
            f"completion={usage.get('completion_tokens','?')} tokens"
        )

        # ── Unload BEFORE we do JSON parsing so VRAM frees even if parsing fails ──
        if unload_after:
            _unload_all_models(server_url, wait_seconds=unload_wait_seconds)

        # ── Strip thinking tokens ──────────────────────────────────────────────
        # Thinking models (Qwen3, etc.) wrap their reasoning in <think>...</think>
        # before emitting the actual answer.
        cleaned = _strip_thinking(raw_content)

        # Strip markdown fences if the model wrapped anyway
        if cleaned.startswith("```"):
            cleaned = "\n".join(
                line for line in cleaned.split("\n")
                if not line.strip().startswith("```")
            ).strip()

        # Validate + re-serialize to guarantee clean minified JSON
        try:
            parsed     = json.loads(cleaned)
            clean_json = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
        except json.JSONDecodeError as e:
            print(f"[LlamaCppIdeogramPrompter] WARNING: non-JSON after stripping think blocks.")
            print(f"  Parse error: {e}")
            print(f"  Cleaned text (first 500 chars): {cleaned[:500]}")
            # Return raw cleaned text so the user can see what went wrong
            clean_json = cleaned

        return (clean_json, raw_content)



# ──────────────────────────────────────────────────────────────────────────────
# Registration
# ──────────────────────────────────────────────────────────────────────────────

NODE_CLASS_MAPPINGS = {
    "LlamaCppIdeogramPrompter": LlamaCppIdeogramPrompter,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LlamaCppIdeogramPrompter": "🦙 LlamaCPP → Ideogram Prompt",
}
