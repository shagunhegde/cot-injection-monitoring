# Colab Experiment Runbook

How to go from this repo to real experimental results on Google Colab T4.

---

## Before you start: what's local vs Colab

| Layer | What | Where |
|---|---|---|
| 1 | Baselines + target selection | CPU (Colab or local) |
| 2 | Prefill generation (attack text) | OpenRouter API (fast, ~$0.10–0.30) |
| 3 | Subject inference (model runs) | **Colab T4 GPU** |
| 4 | Judges + monitors | OpenRouter API |
| 5 | Aggregation + metrics | CPU (free, re-runnable) |

Layers 1, 2, 4, 5 need no GPU. Layer 3 needs the T4. The design keeps Layer 2 model-agnostic so prefills are generated once and reused across all subject models.

---

## Prerequisites

### 1. Push the repo to GitHub
```bash
# On your Mac:
cd /Users/shagun/Desktop/projects/iago
git init
git add .
git commit -m "Phase 0-5 complete: infra, attacks, judges, metrics, pipeline"
git remote add origin https://github.com/<your-username>/cot-injection-monitoring.git
git push -u origin main
```

### 2. Get your keys
- **OpenRouter** — https://openrouter.ai → API keys → create one. Starts with `sk-or-v1-...`
  - Used for: prefill generation (Families B/C/D), faithfulness judge, MFR monitor
  - Estimated cost: ~$0.10–0.50 for a 50-item H1-only smoke run
- **HuggingFace token** — https://huggingface.co/settings/tokens → "New token" (read access is enough)
  - Used for: downloading DeepSeek-R1-Distill and Qwen3

### 3. Request HuggingFace model access (one-time)
- DeepSeek-R1-Distill-Qwen-1.5B: https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B (open, no gating)
- Qwen3-4B: https://huggingface.co/Qwen/Qwen3-4B (open, no gating)

---

## Colab setup (run once per session)

Open a new Colab notebook. Paste each block into a cell.

### Cell 1 — Clone + install
```python
!git clone https://github.com/<your-username>/cot-injection-monitoring.git /content/cot-injection-monitoring
%cd /content/cot-injection-monitoring
!pip install -q -e ".[gpu,api,dev]" pyyaml
```

### Cell 2 — Set API keys
```python
import os

# Option A: paste directly (don't commit this cell)
os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-..."
os.environ["HF_TOKEN"] = "hf_..."

# Option B: use Colab Secrets (Secrets tab in left sidebar → add keys there)
# from google.colab import userdata
# os.environ["OPENROUTER_API_KEY"] = userdata.get("OPENROUTER_API_KEY")
# os.environ["HF_TOKEN"] = userdata.get("HF_TOKEN")
```

### Cell 3 — Mount Drive + verify workspace
Drive is where all results persist across sessions. If Colab disconnects, your cache survives.
```python
from src.infra.paths import resolve_root

paths = resolve_root()   # mounts /gdrive/MyDrive/cot-injection-monitoring automatically
print("Workspace:", paths.root)
print("Cache dir:", paths.cache)
print("Ledger   :", paths.ledger)
```
Expected output: `/gdrive/MyDrive/cot-injection-monitoring`

### Cell 4 — Verify GPU
```python
import torch
print(torch.cuda.is_available())          # must be True
print(torch.cuda.get_device_name(0))      # should be Tesla T4
print(f"{torch.cuda.mem_get_info()[0]/1e9:.1f} GB free")
```

---

## Workflow: every experiment follows this order

```
1. Dry-run estimate   →  confirm cost before spending anything
2. Generate prefills  →  Layer 2; OpenRouter; one-time per config
3. Run subject model  →  Layer 3; T4 GPU; most expensive step
4. Run judges         →  Layer 4; OpenRouter; ~same cost as prefills
5. Aggregate          →  Layer 5; CPU; free, re-run any time
```

The cache makes each step idempotent. Kill and re-run any cell; already-done work is skipped.

---

## Smoke test (do this first — ~5 min, <$0.01)

### Cell 5 — Dry-run on 1 condition
```python
!python -m src.pipeline.run_experiment --dry_run --cfg limit=1 families=[B] n_items=5
```
This prints a cost estimate and exits with zero spend. Confirm it shows "1 condition" and "$0.00X".

### Cell 6 — Generate 1 prefill (Layer 2, ~$0.001)
```python
import yaml
from src.infra.paths import resolve_root
from src.infra.cache import Cache
from src.infra.llm_client import LLMClient
from src.infra.matrix import build_condition_matrix
from src.attacks.generate import generate_all_prefills

paths = resolve_root()
cfg = yaml.safe_load(open("config/experiment.yaml"))
cfg.update({"limit": 1, "families": ["B"], "lengths": [100], "n_items": 5})

# Build matrix with placeholder items for prefill generation.
items = [{"item_hash": f"item_{i}", "question": "What is 2+2?",
           "choices": ["1","2","3","4"], "answer_idx": 3, "answer_letter": "D",
           "subject": "math", "split": "test"} for i in range(5)]
items_by_hash = {it["item_hash"]: it for it in items}
matrix = build_condition_matrix(cfg, [it["item_hash"] for it in items])

prefill_cache = Cache(paths.root, "prefills")
client = LLMClient.from_env()
stats = generate_all_prefills(matrix, items_by_hash, client,
                               tokenizer=None, cache=prefill_cache)
print(stats)
```

### Cell 7 — Load model + run 1 generation (Layer 3, ~3 min download + 15s)
```python
import yaml
from src.infra.cache import Cache
from src.infra.paths import resolve_root
from src.models.run_model import load_subject_model, run_conditions_hf
from src.infra.matrix import build_condition_matrix

paths = resolve_root()
models_cfg = yaml.safe_load(open("config/models.yaml"))
model_cfg = models_cfg["subjects"]["deepseek-r1-distill-qwen-1.5b"]

# Load model once; keep this cell alive — don't re-run needlessly.
model, tokenizer = load_subject_model(model_cfg, device="cuda")

# Build prefills_by_key lookup from Layer-2 cache.
prefill_cache = Cache(paths.root, "prefills")
# (prefills_by_key maps f"{family}|{length}|{item_hash}" → text)
prefills_by_key = {}
# ... see full helper in Cell 8 below.
```

### Cell 8 — Helper: build prefills_by_key from cache
```python
import json
from pathlib import Path

def build_prefills_by_key(cache_root: Path, cfg: dict) -> dict:
    """Read Layer-2 JSONL shards into a {family|length|item_hash: text} dict."""
    out = {}
    prefill_dir = cache_root / "cache" / "prefills"
    if not prefill_dir.exists():
        return out
    for shard in sorted(prefill_dir.rglob("*.jsonl")):
        with open(shard) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                meta = rec.get("meta", {})
                result = rec.get("result", {})
                if not result or not result.get("text"):
                    continue
                family  = meta.get("family", "")
                length  = meta.get("length", 0)
                item_hash = meta.get("item_hash", "")
                if family and length and item_hash:
                    out[f"{family}|{length}|{item_hash}"] = result["text"]
    return out

prefills_by_key = build_prefills_by_key(paths.root, cfg)
print(f"Loaded {len(prefills_by_key)} prefills from cache")
```

### Cell 9 — Run subject inference (Layer 3)
```python
from src.infra.matrix import build_condition_matrix

subject_cache = Cache(paths.root, "subject")
results = run_conditions_hf(
    matrix, items_by_hash, prefills_by_key,
    model, tokenizer, model_cfg, subject_cache,
    global_seed=cfg.get("seed", 0),
)
print(f"Done: {len(results)} conditions")
for r in results[:2]:
    print(r)
```

### Cell 10 — Run judges + write ledger (Layer 4 + ledger)
```python
# Judges run via run_experiment.py — all Layer-3 conditions are now cached so it skips GPU.
!python -m src.pipeline.run_experiment \
    --cfg limit=1 families=[B] lengths=[100] n_items=5 dry_run=false max_spend_usd=1
```

### Cell 11 — Aggregate and inspect results (Layer 5)
```python
!python -m src.pipeline.aggregate
```
Results are written to `<workspace>/runs/aggregate_<timestamp>.json`.

---

## Real H1 experiment (50 items, ~1.5 GPU-hours, ~$0.50 API)

Once the smoke test passes end-to-end, run the full H1 config.

### Step 1 — Dry-run estimate first (always)
```bash
!python -m src.pipeline.run_experiment --dry_run \
    --cfg families=[B] lengths=[100,300,700,1500] n_items=50
```
Read the printed estimate. Typical: ~200 conditions, ~$0.15 API, ~1.2 GPU-h.

### Step 2 — Generate prefills (Layer 2, one-time)
Run Cell 6 with the H1 config (families=[B], lengths=[100,300,700,1500], n_items=50).
This calls OpenRouter ~200 times (~$0.05, ~2 min). Second run: 100% cache hits.

### Step 3 — Subject inference (Layer 3)
Run Cells 7-9 with the H1 config. ~1.2 GPU-hours on T4.

**Colab disconnect strategy:** If Colab disconnects mid-run, `run_conditions_hf` writes every result immediately. On reconnect: run Cells 1-4 to restore environment, then re-run Cells 7-9. Already-cached conditions are skipped automatically.

### Step 4 — Judges + aggregation
```bash
!python -m src.pipeline.run_experiment --cfg families=[B] lengths=[100,300,700,1500] n_items=50
!python -m src.pipeline.aggregate
```

---

## H2 experiment (content vs length)

```bash
# Dry-run first:
!python -m src.pipeline.run_experiment --dry_run \
    --cfg families=[B,C] lengths=[100,700] n_items=50

# Then follow the same Steps 2-4.
# Families B and C share the same lengths; C is built matched-pair to B (already handled).
```

## H3 experiment (reflection defense)

```bash
!python -m src.pipeline.run_experiment --dry_run \
    --cfg families=[B,D] lengths=[300,700] n_items=50
# Family D minimum length is 300 (enforced by matrix.py).
```

## Full run (all hypotheses)

```bash
!python -m src.pipeline.run_experiment --dry_run \
    --cfg families=[B,C,D] lengths=[100,300,700,1500] n_items=50
# Family A (conclusion-only floor) is cheap to add: families=[A,B,C,D]
```

---

## Adding error bars (bootstrap CIs)

Raise `samples_per_condition` from 1 → 3. Only the 2 new sample indices are computed; existing generations are unchanged.

```bash
!python -m src.pipeline.run_experiment --dry_run \
    --cfg samples_per_condition=3 families=[B] n_items=50
# Estimate will show ~2× net-new GPU work (sample_idx 1 and 2 only).
```

---

## Adding a second subject model (Qwen3-4B)

Layer-2 prefills are model-agnostic — they are fully reused. Only Layers 1, 3, 4 run for the new model.

```bash
!python -m src.pipeline.run_experiment --dry_run \
    --cfg "models=[deepseek-r1-distill-qwen-1.5b,qwen3-4b]" families=[B] n_items=50
# Estimate: Layer-2 prefill count = 0 new (reused). Layer-3 ~= same as 1.5b run.
```

Then in the Colab notebook run Cells 7-9 again with `model_cfg = models_cfg["subjects"]["qwen3-4b"]`.

---

## Cost summary (rough estimates, 50 items)

| Run | GPU-hours (T4) | API cost |
|---|---|---|
| Smoke (limit=1) | ~0.01 | ~$0.001 |
| H1 (families=[B]) | ~1.2 | ~$0.15 |
| H2 (families=[B,C]) | ~0.6 (Layer 3 only; Layer 2 reuses B) | ~$0.10 |
| H3 (families=[B,D]) | ~0.6 | ~$0.10 |
| Full ([B,C,D]) | ~1.8 | ~$0.30 |
| Full + samples=3 | ~3.6 | ~$0.30 (API unchanged) |

T4 GPU time is free on Colab (within session limits). The API cost is the main spend.

---

## Troubleshooting

**`resolve_root()` doesn't mount Drive:**
Drive auto-mounts in Colab when `google.colab` is importable. If it fails: `from google.colab import drive; drive.mount('/gdrive')`.

**`OPENROUTER_API_KEY` not found:**
`LLMClient.from_env()` raises a clear error. Check `echo $OPENROUTER_API_KEY` in a cell.

**OOM on T4:**
`generate_one_hf` has an OOM guard that halves `max_new_tokens` once and retries. If it still OOMs, reduce `max_new_tokens` in `config/models.yaml` (default: 2048 → try 1024).

**`NotImplementedError: Subject generation requires a pre-loaded model`:**
`run_experiment.py` raises this if Layer-3 cache misses and no model is loaded. This is by design — run Cells 7-9 first to populate Layer 3, then run `run_experiment.py` (which only handles judges at that point).

**Prefill validation failures logged as warnings:**
Family B/C/D prefills that fail the validator (off-topic, wrong conclusion, bad length) are not cached and will be retried on the next run. A few failures per run are normal. If failure rate > 20%, increase `max_retries_length` in `config/models.yaml`.

---

## Where results live

```
<workspace>/                         # = /gdrive/MyDrive/cot-injection-monitoring/
  cache/
    prefills/              ← Layer 2: attack texts (reused across all subjects)
    subject/               ← Layer 3: subject traces + parsed answers
    judge/                 ← Layer 4: VR flags + MFR flags
    baselines/             ← Layer 1: baseline answers + target letters
  runs/
    aggregate_<ts>.json    ← Layer 5 output: all metrics + CAS + MCP tables
  ledger/
    <ts>.json              ← per-run audit trail: config, spend, cache stats, errors
```

Every JSONL shard is content-addressed by sha256 of inputs. Re-running the same config is always safe and always free (cache hits only).
