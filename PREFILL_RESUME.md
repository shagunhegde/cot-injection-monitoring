# Prefill Resume — Complete the 200 B-family prefills

Run these two cells **in order** after reconnecting Colab.
Before starting: run Cells 1–4 from COLAB_CELLS.md (git reset, set keys, mount Drive, verify GPU).

---

## Step 1 — Load model + tokenizer

Run this first. It takes ~3 min on first run (model cached on Drive after that).

```python
import os, yaml
os.chdir("/content/cot-injection-monitoring")

from src.infra.paths import resolve_root
from src.models.run_model import load_subject_model

paths = resolve_root()
os.environ["HF_HOME"] = str(paths.root / "hf_home")

models_cfg = yaml.safe_load(open("config/models.yaml"))
model_cfg = models_cfg["subjects"]["deepseek-r1-distill-qwen-1.5b"]

model, tokenizer = load_subject_model(model_cfg, device="cuda")
print("Model loaded:", model_cfg["hf_id"])
print("Tokenizer vocab size:", tokenizer.vocab_size)
```

---

## Step 2 — Resume prefill generation (skips the 115 already cached)

Run this after Step 1. Expects `model` and `tokenizer` in scope from Step 1.
Will skip all 115 already-cached prefills and generate the remaining ~85.
Expected output: `cached=112, generated≈38, failed≈0`.

```python
import os, yaml
os.chdir("/content/cot-injection-monitoring")

from src.infra.paths import resolve_root
from src.infra.cache import Cache
from src.infra.llm_client import LLMClient
from src.infra.matrix import build_condition_matrix
from src.data.load_mmlu import load_mmlu
from src.data.select_target import uniform_logprobs
from src.pipeline.baseline import get_or_cache_baseline, get_or_cache_target
from src.pipeline.run_experiment import _resolve_targets
from src.attacks.generate import generate_all_prefills

paths = resolve_root()
cfg = yaml.safe_load(open("config/experiment.yaml"))
cfg.update({"families": ["B"], "lengths": [100, 300, 700], "n_items": 50})

# Load same 50 MMLU items (deterministic — same order every time)
items = load_mmlu(subjects="all", split="test", n_items=50,
                  cache_dir=paths.root / "data" / "hf_cache")
items_by_hash = {it["item_hash"]: it for it in items}
print(f"Loaded {len(items)} items")

# Resolve targets from cache (no GPU needed — reads Layer 1 cache)
baseline_cache = Cache(paths.root, "baselines")
target_cache   = Cache(paths.root, "targets")

def _dummy_generate(item):
    return {"answer_letter": item["answer_letter"],
            "option_logprobs": uniform_logprobs(len(item["choices"]))}

for item in items:
    bl = get_or_cache_baseline(item, "deepseek-r1-distill-qwen-1.5b",
                               baseline_cache, generate_fn=_dummy_generate)
    if bl:
        get_or_cache_target(item, "deepseek-r1-distill-qwen-1.5b",
                            bl, target_cache, target_mode="most_plausible")

raw_matrix = build_condition_matrix(cfg, [it["item_hash"] for it in items])
matrix = _resolve_targets(raw_matrix, items_by_hash, cfg, paths,
                          baseline_cache, target_cache)
print(f"Matrix: {len(matrix)} conditions")

# Generate missing prefills — uses real tokenizer for accurate length control
prefill_cache = Cache(paths.root, "prefills")
client = LLMClient.from_env()

stats = generate_all_prefills(
    matrix, items_by_hash, client,
    tokenizer=tokenizer,        # real DeepSeek tokenizer from Step 1
    cache=prefill_cache,
    generator_model="claude-haiku-4-5-20251001",
)
print(f"Prefill stats: {stats}")
print(f"  cached={stats.cached}  generated={stats.generated}  failed={stats.failed}")
```

---

## What to run next

Once `cached=112, generated=38, failed=0` (total 150), proceed to **Scaled Cell 8** below.
The 3 B_1500 prefills already in cache are left alone — not included in subject inference.

---

## Scaled Cell 8 — Subject inference (150 conditions)

Requires: Step 1 (model loaded), Step 2 complete (150 prefills cached).

```python
import os, yaml
os.chdir("/content/cot-injection-monitoring")

from src.infra.paths import resolve_root
from src.infra.cache import Cache
from src.infra.matrix import build_condition_matrix
from src.data.load_mmlu import load_mmlu
from src.data.select_target import uniform_logprobs
from src.pipeline.baseline import get_or_cache_baseline, get_or_cache_target
from src.pipeline.run_experiment import _resolve_targets
from src.attacks.generate import prefill_cache_key, prefill_shard
from src.models.run_model import run_conditions_hf

paths = resolve_root()
cfg = yaml.safe_load(open("config/experiment.yaml"))
cfg.update({"families": ["B"], "lengths": [100, 300, 700], "n_items": 50})

items = load_mmlu(subjects="all", split="test", n_items=50,
                  cache_dir=paths.root / "data" / "hf_cache")
items_by_hash = {it["item_hash"]: it for it in items}

baseline_cache = Cache(paths.root, "baselines")
target_cache   = Cache(paths.root, "targets")
def _dummy_generate(item):
    return {"answer_letter": item["answer_letter"],
            "option_logprobs": uniform_logprobs(len(item["choices"]))}
for item in items:
    bl = get_or_cache_baseline(item, "deepseek-r1-distill-qwen-1.5b",
                               baseline_cache, generate_fn=_dummy_generate)
    if bl:
        get_or_cache_target(item, "deepseek-r1-distill-qwen-1.5b",
                            bl, target_cache, target_mode="most_plausible")

raw_matrix = build_condition_matrix(cfg, [it["item_hash"] for it in items])
matrix = _resolve_targets(raw_matrix, items_by_hash, cfg, paths,
                          baseline_cache, target_cache)
print(f"Resolved {len(matrix)} conditions")

prefill_cache    = Cache(paths.root, "prefills")
generator_model  = "claude-haiku-4-5-20251001"
prompt_version   = "v1"

prefills_by_key = {}
for cond in matrix:
    key   = prefill_cache_key("mmlu", cond["item"], cond["family"], cond["length"],
                               cond["target"], generator_model, prompt_version)
    shard = prefill_shard("mmlu", cond["family"], cond["length"])
    rec   = prefill_cache.get(shard, key)
    if rec and rec.get("text"):
        prefills_by_key[f"{cond['family']}|{cond['length']}|{cond['item']}"] = rec["text"]

print(f"Prefills loaded: {len(prefills_by_key)} / {len(matrix)}")

models_cfg  = yaml.safe_load(open("config/models.yaml"))
model_cfg   = models_cfg["subjects"]["deepseek-r1-distill-qwen-1.5b"]
subject_cache = Cache(paths.root, "subject")

results = run_conditions_hf(
    matrix, items_by_hash, prefills_by_key,
    model, tokenizer, model_cfg, subject_cache,
    global_seed=cfg.get("seed", 0),
)
print(f"Done: {len(results)} conditions")
```
