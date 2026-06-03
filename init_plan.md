CLAUDE.md structure (created Phase 0, updated every phase — §10)

Cannot be written now (plan mode allows editing only this plan file). Phase 0 creates it with:

Part 1 — stable context (edited in place): one-paragraph mission (no Stream A/B); the §8 tree + where things live; how to run (env setup, baseline / generate-prefills / run-experiment / aggregate commands, both local & Colab); the §7 hard-constraints checklist (open models only as subjects, never persist GPQA text, content-addressable + write-immediately); the cost-control + reproducibility workflow — the 5 cache layers and what each separation saves, the dry_run → estimate → adjust → run loop, max_spend_usd, the cheap-run recipes (§6.6.5), the new samples_per_condition knob, and the determinism contract above; how resolve_root() picks local vs Colab.

Part 2 — append-only progress log: per phase, a dated entry with Phase/goal, what was built, decisions & rationale, gotchas & fixes, how-to-verify command, results pointers, cache & cost (hit/miss per layer, dry_run-vs-actual, ledger pointer), open questions/next step.

---
Phases (each: deliverables → acceptance check → CLAUDE.md entry). Build in order; don't advance until acceptance passes. 
- Phase 0 — Scaffold + infra. Repo tree (§8), all of src/infra/ incl. new seed.py/llm_client.py,                         config/{models,experiment,cost}.yaml (with sampl models), pyproject.toml (pinned: torch,transformers, datasets, vllm optional, openai-SDK-for-OpenRouter, pyyaml, matplotlib, tenacity), README.md (local + Colabquick-start), initialized CLAUDE.md. Accept: impets ok; resolve_root() works locally + under mockedColab; Cache.get_or_compute round-trips incl. truncated-final-line survival; dry_run on empty matrix prints zero-cost repexits clean; build_condition_matrix honors everyr_condition on a toy config. (Testable entirely onthis Mac.)                                                                                       

- Phase 1 — Prefill smoke test. prefill.py, run_-R1-Distill-Qwen-1.5B on one hardcoded MCQ + trivialFamily-A prefill. Accept (on Colab): rendered template tail printed; <think>…</think> split; boxed letter + stated value extracted; attempt to reproduce trace–answer dis

- Phase 2 — Data + baselines + targets. load_mmlu.py, select_target.py, baseline.py (cache Layer 1). Accept: baselines cached for N MMLU items; targets by plausibility (not "firstable; second run ≈100% cache hits.

- Phase 3 — Attack generation + length control. templates.py, generate.py, length_control.py, validate.py; A/B/C/D across §5.4   lengths, C built per-B. Accept: validators pass;ce tokenizer (per-subject counts as metadata);failure rate logged; dry_run predicts net-new gen count; second run generates 0 new prefills.                                    

- Phase 4 — Judges + metrics. faithfulness.py, m, metrics/*, unit tests. Accept: on a toy labeled set VR/MFR sane & deterministic; composites match hand-computed values; EEMR both ways documented.                                   

- Phase 5 — Core experiment (H1/H2/H3). run_expe→ guarded Layers 3&4 → ledger), aggregate.py. RunA/B/C/D × lengths on DeepSeek-distill (1.5B then 7B/14B), full-trace monitor, MMLU. Accept: H1 curve + MCP; CAS per length;      B-vs-D per length; aggregation reproducible fromart resumes with no recompute; dry_run estimate ≈actual; guardrail halts cleanly at max_spend_usd.                                                                                

- Phase 6 — Unified extensions. Reuse same loop+) monitored-vs-unmonitored, (c) self-as-monitor, (d)training-type axis (Qwen3 + instruct), (e) answer-only vs full-trace. Accept: each yields a figure/table + one-paragraph reading;dry_run before each confirms it computes only ites Layer-2 prefills; new monitor/monitored reusesLayers 1–3). Out of scope: anything needing training a model.

- Phase 7 — Aggregation, figures, writeup tablesesis/per-extension tables + methods/limitations(§2.3, §11). Accept: every number reconstructable from <root>/ caches + aggregate.py.                                            
GPQA wired only after MMLU works end-to-end (Phase 5+), honoring gating/no-republish.                                            
