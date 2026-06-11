<img width="775" height="892" alt="image" src="https://github.com/user-attachments/assets/7aa213db-2d24-40c2-a618-b3bf22bfc549" />



# ComfyUI LlamaCPP → Ideogram Prompt

A ComfyUI custom node that calls your **local llama.cpp server** to expand a short idea into a rich, structured JSON prompt for **Ideogram 4** — with full thinking/reasoning support.

## What it does

- Connects to your local llama.cpp server (`/v1/chat/completions`)
- Selects from live models via dropdown (auto-populated from `/v1/models`)
- Enables **deep reasoning** via `thinking_budget_tokens` — Qwen3, DeepSeek-R1 and similar models will actually use their `<think>` block
- Strips `<think>...</think>` blocks so only clean JSON reaches Ideogram
- Validates and minifies the JSON output
- Optionally **unloads the model after generation** to free VRAM before diffusion runs

## Requirements

- [ComfyUI](https://github.com/comfyanonymous/ComfyUI)
- [llama.cpp server](https://github.com/ggml-org/llama.cpp) running locally in router mode
- A thinking-capable model loaded (tested with `unsloth/Qwen3.6-27B-MTP-GGUF`, `unsloth/Qwen3.6-35B-A3B-GGUF`)
- Ideogram 4 set up in ComfyUI (model + VAE + CLIP)

## Installation

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/iChristGit/comfyui-llamacpp-ideogram
```

Restart ComfyUI. The node appears under **LlamaCPP / Ideogram**.

## Node: 🦙 LlamaCPP → Ideogram Prompt

### Inputs

| Input | Type | Default | Description |
|---|---|---|---|
| `user_idea` | STRING | — | Short natural-language description of what you want |
| `aspect_ratio` | STRING | `9:16` | Target aspect ratio (e.g. `1:1`, `16:9`, `9:16`, `4:5`) |
| `model` | DROPDOWN | — | Live model list from your llama.cpp server |
| `server_url` | STRING | `http://127.0.0.1:8082` | Base URL of your llama.cpp server |
| `temperature` | FLOAT | `0.6` | Sampling temperature |
| `max_tokens` | INT | `8192` | Max tokens including thinking tokens |
| `unload_after` | BOOLEAN | `true` | Unload model after generation to free VRAM |
| `unload_wait_seconds` | INT | `3` | Seconds to wait after unload for VRAM reclaim |
| `enable_thinking` | BOOLEAN | `true` | Enable deep reasoning (`thinking_budget_tokens`) |
| `thinking_budget` | INT | `4096` | Max tokens the model may spend reasoning |
| `system_prompt_override` | STRING | *(optional)* | Override the built-in system prompt |

### Outputs

| Output | Type | Description |
|---|---|---|
| `ideogram_json` | STRING | Clean minified JSON ready for Ideogram's CLIPTextEncode |
| `raw_response` | STRING | Full raw response including `<think>` block (for debugging) |

## llama.cpp server setup

The node expects your llama.cpp server running in **router mode** on port `8082`. Example `start-llamacpp.bat`:

```bat
"C:\Llama CPP\llama-server.exe" --port 8082 --router ...
```

### Thinking budget

The node sends `thinking_budget_tokens` per request — no server-side `--reasoning-budget` flag needed. You'll see this in your llama.cpp logs when it's working:

```
I reasoning-budget: activated, budget=4096 tokens
...
I reasoning-budget: deactivated (natural end)
```

`natural end` means the model finished reasoning on its own before hitting the cap — ideal quality. If you see `forced end`, increase the budget.

## Example Workflow

The `example_workflows/ideogram4_t2i.json` file is a **ready-to-use ComfyUI workflow** — just drag and drop it into ComfyUI to get started immediately.

It is based on the **official ComfyUI Ideogram 4 txt2img workflow**, with one addition: the **🦙 LlamaCPP → Ideogram Prompt** node wired in as the prompt source. Everything else — the model loader, VAE, CLIP, sampler, and save node — is identical to the stock workflow.

To use it you will need:
- The Ideogram 4 model weights (`ideogram4_fp8_scaled.safetensors`)
- The unconditional model (`ideogram4_unconditional_fp8_scaled.safetensors`)
- CLIP model (`qwen3vl_8b_fp8_scaled.safetensors`)
- VAE (`flux2-vae.safetensors`)
- Your llama.cpp server running on `http://127.0.0.1:8082`

> Adjust the model filenames to match whatever you have named them locally.

## Example output

The images below was generated with:
- Model: `unsloth/Qwen3.6-27B-MTP-GGUF:Q4_K_XL`
- Thinking budget: `4096`
- Prompt: *"an episode of SpongeBob/south park was banned in the United States for being quite offensive. Generate a screenshot of that episode."*

<img width="1376" height="768" alt="Ideogram_4 0_00087_" src="https://github.com/user-attachments/assets/d4615595-3a29-4bd1-9867-c466e8ba3d52" />

<img width="1456" height="1456" alt="Ideogram_4 0_00046_" src="https://github.com/user-attachments/assets/855f3a91-b2fd-463b-ae4c-ef3aa6f2bcb2" />

## Tips

- **For complex scenes** (multi-character, comic panels, text-heavy): increase `thinking_budget` to `6144`–`8192`
- **For simple product shots**: `thinking_budget` of `1024`–`2048` is plenty and saves time
- **Temperature**: `0.6` is a good default. Go lower (`0.3`) for more consistent JSON structure, higher (`0.8`) for more creative descriptions
- **`unload_after = true`** is strongly recommended — the LLM and diffusion model fighting over VRAM will cause OOM errors on most consumer GPUs

## License

MIT
