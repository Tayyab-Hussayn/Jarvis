# tools/

Developer utilities. Not part of the assistant runtime.

## eval_models.py

Scores candidate LLMs on Kravein's **actual** workload, not a generic
benchmark. Model rankings routinely invert between the two — see below.

```bash
.venv/bin/python tools/eval_models.py MODEL [MODEL ...]
.venv/bin/python tools/eval_models.py --restraint MODEL [MODEL ...]
```

It probes five things that matter for an agent holding 118 tools on a live
desktop:

| Case | What it catches |
|---|---|
| `chain` | multi-step where step 2 depends on step 1 |
| `structured` | deeply nested arguments (arrays of objects) |
| `judgment` | vague request → investigate, don't start destroying |
| `honesty` | asks for a capability that doesn't exist → say so |
| `precision` | "thirty seven percent" → exactly `37` |

`--restraint` runs an open-ended request repeatedly and reports how many tool
calls it burns, whether it ever answers, and whether it reaches a destructive
tool. This is the single most discriminating test.

### Why a "say OK" benchmark is misleading

Latency rankings **invert** once the real 118-tool schema is attached:

| Model | bare "say OK" | with 118 tools |
|---|---|---|
| `anthropic/claude-haiku-4.5` | **1.8s** | 4.4s median, 15–53s spread |
| `deepseek/deepseek-v3.2` | 6.0s | **2.65s** |
| `moonshotai/kimi-k2` | 2.1s | 7.0s |

And single-call latency is the wrong metric anyway. What matters is
**calls-to-completion**: deepseek answers a single call fast but burns ~20
calls on a vague request, so it is far slower end to end than a model that
finishes in 4.

### Cost: read the right field

The gateway returns several cost fields and they mean different things:

```json
"usage": {
  "cost": 0.000129,                      // what YOUR account is charged
  "cost_details": {
    "upstream_inference_cost": 0.0029    // what Cline pays upstream - not yours
  },
  "is_byok": true                        // billed to your own key, not Cline
}
```

Read **`cost`**. Reading `upstream_inference_cost` produced a completely
inverted picture once during development.

Note `cost: 0` with `is_byok: true` means "billed to a BYOK key on that
account", **not** "this model is free".

## Free models

Only models whose id ends in `:free` are genuinely free. Everything else needs
a non-zero Cline balance, even when it reports `cost: 0`.

Verified reachable at a **$0 balance**, all support tool calling:

```
cohere/north-mini-code:free
dots-studio/dots-3-note-preview:free
google/gemma-4-26b-a4b-it:free
google/gemma-4-31b-it:free
nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free
nvidia/nemotron-3-super-120b-a12b:free
nvidia/nemotron-3-ultra-550b-a55b:free
nvidia/nemotron-3.5-lightning:free
poolside/laguna-s-2.1:free
poolside/laguna-xs-2.1:free
liquid/lfm-2.5-2.6b:free          (2.6B - too small to be useful here)
```

Catalogue prices for the paid models, from Cline's own bundle
(`@cline/llms/dist/models.js`), per 1M input/output tokens:

| Model | Price |
|---|---|
| `anthropic/claude-haiku-4.5` | $1.00 / $5.00 |
| `moonshotai/kimi-k2` | $0.632 / $2.53 |
| `google/gemini-3-flash-preview` | $0.50 / $3.00 |
| `deepseek/deepseek-v3.2` | $0.29 / $0.43 |
| `minimax/minimax-m2.5` | $0.25 / $1.20 |
| `nvidia/nemotron-3.5-lightning:free` | **$0 / $0** |

Our requests carry ~10k prompt tokens because of the tool schema, so input
price dominates. Editing the local catalogue does **not** change what you are
charged — pricing is computed server-side and returned in the response.
