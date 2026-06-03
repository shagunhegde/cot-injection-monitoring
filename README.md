# CoT-Injection Monitoring

Measuring the robustness of chain-of-thought (CoT) monitoring when an attacker controls part of a model's `<think>` trace.

**Three hypotheses:**
- **H1** — monitorability degrades non-linearly with injection length
- **H2** — degradation is content-driven, not mere verbosity (Family B vs C)
- **H3** — an embedded reflection step changes monitorability (B vs D; sign not pre-committed)

**See `CLAUDE.md`** for the full architecture, cost-control workflow, and phase-by-phase progress log.

---

## Quick start — local (no GPU; scaffold + dry-run only)

```bash
git clone <repo> && cd cot-injection-monitoring
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" pyyaml

# Optional: OpenRouter key for judge/generator calls
export OPENROUTER_API_KEY=sk-or-...

# Estimate run cost (no spend, no GPU):
python -m src.pipeline.run_experiment --dry_run

# Run tests:
pytest tests/ -v
```

Override any config knob on the CLI:
```bash
python -m src.pipeline.run_experiment --dry_run --cfg n_items=10 families=[B] lengths=[100,700]
```

---

## Quick start — Google Colab (subject inference)

```python
# Cell 1: clone + install
!git clone <repo_url> /content/cot-injection-monitoring
%cd /content/cot-injection-monitoring
!pip install -q -e ".[gpu,api,dev]" pyyaml

# Cell 2: set keys + mount Drive (resolve_root() does this automatically)
import os
os.environ["OPENROUTER_API_KEY"] = "sk-or-..."
# Optionally override Drive root:
# os.environ["COTIM_COLAB_ROOT"] = "/gdrive/MyDrive/my-custom-path"

# Cell 3: verify workspace resolves
from src.infra.paths import resolve_root
paths = resolve_root()   # mounts Drive, creates workspace dirs
print(paths.root)

# Cell 4: dry-run estimate
!python -m src.pipeline.run_experiment --dry_run
```

---

## Config knobs (all in `config/experiment.yaml`)

| Knob | Default | Controls |
|---|---|---|
| `n_items` | 50 | MCQ items per dataset |
| `samples_per_condition` | 1 | Subject generations per condition (raise for error bars) |
| `families` | `[B,C,D]` | Attack families to run |
| `lengths` | `[100,300,700,1500]` | Injection lengths (tokens) |
| `models` | `[1.5b]` | Subject model(s) |
| `targets` | `most_plausible` | Distractor selection |
| `dry_run` | `false` | `true` = estimate only, zero spend |
| `max_spend_usd` | 50 | Spend guardrail |
| `limit` | `null` | `1` = single-condition smoke test |

**Cheap-run recipes:**

```yaml
# H1 only (length dose-response):
families: [B]
n_items: 50

# Single smoke condition:
limit: 1
dry_run: true

# Add error bars (3× GPU cost):
samples_per_condition: 3
```

---

## Build phases

| Phase | What | Status |
|---|---|---|
| 0 | Scaffold + infra + tests | **Done** |
| 1 | Prefill smoke test (Colab) | Pending |
| 2 | Data + baselines + targets | Pending |
| 3 | Attack generation + length control | Pending |
| 4 | Judges + metrics | Pending |
| 5 | Core experiment H1/H2/H3 | Pending |
| 6 | Unified extensions (size sweep, etc.) | Pending |
| 7 | Figures + writeup tables | Pending |

---

## Key files

| File | Purpose |
|---|---|
| `CLAUDE.md` | Architecture + cost-control workflow + phase log |
| `config/experiment.yaml` | All run-volume knobs |
| `src/infra/matrix.py` | Condition matrix builder |
| `src/infra/cache.py` | Content-addressable cache |
| `src/pipeline/run_experiment.py` | Experiment runner entrypoint |
