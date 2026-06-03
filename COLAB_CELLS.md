# Colab Cells — Clean Reference

Paste each block into a separate Colab cell and run them in order.
Every cell is self-contained: no partial variables from prior cells needed except
where explicitly noted (model/tokenizer from Cell 4 are needed in Cell 6).

---

## Cell 1 — Clone + install

```python
import os
if not os.path.exists("/content/cot-injection-monitoring"):
    os.system("git clone https://github.com/shagunhegde/cot-injection-monitoring.git /content/cot-injection-monitoring")
else:
    os.system("cd /content/cot-injection-monitoring && git fetch origin && git reset --hard origin/main")

os.chdir("/content/cot-injection-monitoring")
os.system("pip install -q -e '.[gpu,api,dev]' pyyaml")
print("Done")
```

---

## Cell 2 — Set API keys

```python
import os
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-..."   # paste your key here
os.environ["HF_TOKEN"] = "hf_..."                # paste your HF token here
print("Keys set")
```

---

## Cell 3 — Mount Drive + verify workspace

```python
import os
os.chdir("/content/cot-injection-monitoring")

from src.infra.paths import resolve_root
paths = resolve_root()
print("Workspace:", paths.root)
print("Cache dir:", paths.cache)
print("Ledger   :", paths.ledger)
```

---

## Cell 4 — Verify GPU

```python
import torch
print("CUDA available:", torch.cuda.is_available())
print("Device        :", torch.cuda.get_device_name(0))
print(f"Free memory   : {torch.cuda.mem_get_info()[0]/1e9:.1f} GB")
```

---

## Cell 5 — Dry-run estimate

```python
import os
os.chdir("/content/cot-injection-monitoring")
os.system("python -m src.pipeline.run_experiment --dry_run --cfg limit=1 families=[B] n_items=5")
```

---

## Cell 6 — Load MMLU + resolve targets + generate prefills (Layers 1 + 2)

Requires: Cell 2 (ANTHROPIC_API_KEY set), Cell 3 (paths).

```python
import os, yaml
os.chdir("/content/cot-injection-monitoring")

from src.infra.paths import resolve_root
from src.infra.cache import Cache
from src.infra.llm_client import LLMClient
from src.infra.matrix import build_condition_matrix
from src.data.load_mmlu import load_mmlu
from src.data.select_target import uniform_logprobs, select_targets
from src.pipeline.baseline import get_or_cache_baseline, get_or_cache_target
from src.attacks.generate import generate_all_prefills
from src.attacks.length_control import MockTokenizer

paths = resolve_root()

cfg = yaml.safe_load(open("config/experiment.yaml"))
cfg.update({"limit": 1, "families": ["B"], "lengths": [100], "n_items": 5})

# Layer 1: load real MMLU items
items = load_mmlu(
    subjects="all",
    split="test",
    n_items=5,
    cache_dir=paths.root / "data" / "hf_cache",
)
items_by_hash = {it["item_hash"]: it for it in items}
print(f"Loaded {len(items)} MMLU items")
print("Example:", items[0]["question"][:80], "| answer:", items[0]["answer_letter"])

# Layer 1: resolve targets (uniform logprobs fallback — no GPU needed)
baseline_cache = Cache(paths.root, "baselines")
target_cache = Cache(paths.root, "targets")

def _dummy_generate(item):
    lp = uniform_logprobs(len(item["choices"]))
    return {"answer_letter": item["answer_letter"], "option_logprobs": lp}

model_id = "deepseek-r1-distill-qwen-1.5b"
for item in items:
    bl = get_or_cache_baseline(item, model_id, baseline_cache,
                               generate_fn=_dummy_generate)
    if bl:
        get_or_cache_target(item, model_id, bl, target_cache,
                            target_mode="most_plausible")

# Build condition matrix and resolve target placeholders to real letters
raw_matrix = build_condition_matrix(cfg, [it["item_hash"] for it in items])

from src.pipeline.run_experiment import _resolve_targets
import yaml as _yaml
models_cfg = _yaml.safe_load(open("config/models.yaml"))
matrix = _resolve_targets(raw_matrix, items_by_hash, cfg, paths,
                          baseline_cache, target_cache)
print(f"Matrix: {len(raw_matrix)} raw → {len(matrix)} resolved conditions")
for c in matrix[:3]:
    print(f"  item={c['item'][:8]}... family={c['family']} length={c['length']} target={c['target']}")

# Layer 2: generate prefills via Anthropic API
prefill_cache = Cache(paths.root, "prefills")
client = LLMClient.from_env()
stats = generate_all_prefills(matrix, items_by_hash, client,
                               tokenizer=MockTokenizer(), cache=prefill_cache,
                               generator_model="claude-haiku-4-5-20251001")
print("Prefill stats:", stats)
```

---

## Cell 7 — Load subject model (Layer 3 setup)

Keep this cell alive — do not re-run unless you restart the runtime.
Model download is ~3.5 GB and takes ~3 min on first run; cached on Drive after that.

```python
import os, yaml
os.chdir("/content/cot-injection-monitoring")

from src.infra.paths import resolve_root
from src.models.run_model import load_subject_model

paths = resolve_root()
models_cfg = yaml.safe_load(open("config/models.yaml"))
model_cfg = models_cfg["subjects"]["deepseek-r1-distill-qwen-1.5b"]

import os
os.environ["HF_HOME"] = str(paths.root / "hf_home")

model, tokenizer = load_subject_model(model_cfg, device="cuda")
print("Model loaded:", model_cfg["hf_id"])
```

---

## Cell 8 — Run subject inference (Layer 3)

Requires: Cell 6 (prefills generated), Cell 7 (model loaded).

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
cfg.update({"limit": 1, "families": ["B"], "lengths": [100], "n_items": 5})

items = load_mmlu(subjects="all", split="test", n_items=5,
                  cache_dir=paths.root / "data" / "hf_cache")
items_by_hash = {it["item_hash"]: it for it in items}

# Resolve targets (same as Cell 6 — reads from cache, no API calls)
baseline_cache = Cache(paths.root, "baselines")
target_cache = Cache(paths.root, "targets")
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

# Look up prefills using proper cache keys
prefill_cache = Cache(paths.root, "prefills")
generator_model = "claude-haiku-4-5-20251001"
prompt_version = "v1"

prefills_by_key = {}
for cond in matrix:
    family = cond["family"]
    length = cond["length"]
    item_hash = cond["item"]
    target = cond["target"]
    key = prefill_cache_key("mmlu", item_hash, family, length,
                             target, generator_model, prompt_version)
    shard = prefill_shard("mmlu", family, length)
    rec = prefill_cache.get(shard, key)
    if rec and rec.get("text"):
        prefills_by_key[f"{family}|{length}|{item_hash}"] = rec["text"]
        print(f"  Prefill found: {family}|{length}|{item_hash[:8]}... target={target}")

print(f"Prefills loaded: {len(prefills_by_key)}")

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

---

## Cell 9 — Run judges (Layer 4)

Requires: Layer 3 cache populated by Cell 8.

```python
import os
os.chdir("/content/cot-injection-monitoring")
os.system("python -m src.pipeline.run_experiment --cfg limit=1 families=[B] lengths=[100] n_items=5 dry_run=false max_spend_usd=1")
```

---

## Cell 10 — Aggregate results (Layer 5)

```python
import os
os.chdir("/content/cot-injection-monitoring")
os.system("python -m src.pipeline.aggregate")
```

Results are written to `/gdrive/MyDrive/cot-injection-monitoring/runs/aggregate_<timestamp>.json`.
