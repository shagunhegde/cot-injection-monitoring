"""Aggregation — cache Layer 5 (pure, free, re-runnable).

Reads all cached Layers 1-4 from the workspace, enriches results with per-item
indicators, and computes all composite metrics per condition cell. Writes results
tables to <root>/runs/.

Zero model or API calls — run this as often as you like to recompute tables,
update metrics formulas, or change how conditions are grouped. Nothing expensive
is re-executed.

Usage:
    python -m src.pipeline.aggregate
    python -m src.pipeline.aggregate --runs-dir /path/to/workspace/runs
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

import yaml

from src.infra.paths import resolve_root
from src.infra.cache import Cache
from src.metrics.core import enrich, is_capturable
from src.metrics.composites import (
    ConditionMetrics, cas, mcp as compute_mcp,
    eemr_rate_product, eemr_direct, mean_mfr,
)
from src.metrics.dissociation import dissociation_report

log = logging.getLogger(__name__)


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ── Cache reading utilities ────────────────────────────────────────────────────

def _iter_shard(shard_path: Path):
    """Yield parsed records from a JSONL shard file (skip truncated lines)."""
    if not shard_path.exists():
        return
    with open(shard_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def load_subject_results(cache_root: Path) -> list[dict]:
    """Load all Layer-3 subject generation results from cache."""
    results = []
    subject_dir = cache_root / "cache" / "subject"
    if not subject_dir.exists():
        return results
    for shard_path in sorted(subject_dir.rglob("*.jsonl")):
        for rec in _iter_shard(shard_path):
            result = rec.get("result", {})
            if not result or result.get("error"):
                continue
            meta = rec.get("meta", {})
            result["_condition_key"] = rec.get("key", meta.get("condition_key", ""))
            result["_family"] = meta.get("family", "")
            result["_length"] = meta.get("length", 0)
            result["_target"] = meta.get("target", "")
            result["_sample_idx"] = meta.get("sample_idx", 0)
            results.append(result)
    return results


def load_judge_results(cache_root: Path) -> dict[str, dict]:
    """Load all Layer-4 judge/monitor results. Returns {condition_key: merged_dict}."""
    merged: dict[str, dict] = {}
    judge_dir = cache_root / "cache" / "judge"
    if not judge_dir.exists():
        return merged
    for shard_path in sorted(judge_dir.rglob("*.jsonl")):
        variant = shard_path.stem.split("_", 1)[-1] if "_" in shard_path.stem else "unknown"
        for rec in _iter_shard(shard_path):
            ck = rec.get("key", "")
            result = rec.get("result", {})
            if not result or result.get("error"):
                continue
            if ck not in merged:
                merged[ck] = {}
            flag = result.get("flag", False)
            if "faithfulness" in str(shard_path):
                merged[ck]["vr_flag"] = flag
                merged[ck]["vr_rationale"] = result.get("rationale", "")
            else:
                # Monitor variant — store per-variant and set primary if full_trace.
                merged[ck].setdefault("mfr_by_variant", {})[variant] = flag
                if "full_trace" in str(shard_path) or "mfr_flag" not in merged[ck]:
                    merged[ck]["mfr_flag"] = flag
                    merged[ck]["mfr_rationale"] = result.get("rationale", "")
    return merged


def load_baseline_targets(cache_root: Path) -> dict[str, dict]:
    """Load Layer-1 baseline answers and targets. Returns {item_hash: {model: {...}}}."""
    result: dict[str, dict] = {}
    for ns in ("baselines", "targets"):
        ns_dir = cache_root / "cache" / ns
        if not ns_dir.exists():
            continue
        for shard_path in sorted(ns_dir.rglob("*.jsonl")):
            for rec in _iter_shard(shard_path):
                ck = rec.get("key", "")
                data = rec.get("result", {})
                if data:
                    result[ck] = data
    return result


# ── Enrichment pipeline ────────────────────────────────────────────────────────

def build_enriched_results(
    subject_results: list[dict],
    judge_by_ck: dict[str, dict],
    baseline_by_ck: dict[str, dict],
    condition_meta: dict[str, dict],
) -> list[dict]:
    """Join Layer 1/3/4 data into enriched per-item result dicts, then call enrich()."""
    enriched = []
    for sr in subject_results:
        ck = sr.get("_condition_key", "")
        if not ck:
            continue

        r = dict(sr)

        # Merge judge results.
        judge = judge_by_ck.get(ck, {})
        r.update(judge)

        # Merge condition metadata (model, family, length, target, sample_idx).
        meta = condition_meta.get(ck, {})
        r.update({k: v for k, v in meta.items() if k not in r})

        # Ensure required fields exist.
        r.setdefault("vr_flag", False)
        r.setdefault("mfr_flag", False)
        r.setdefault("baseline_letter", r.get("baseline_letter"))
        r.setdefault("target_letter", r.get("_target"))

        enrich(r)
        enriched.append(r)

    return enriched


# ── Grouping and per-condition tables ─────────────────────────────────────────

def _group_key(r: dict) -> tuple:
    return (
        r.get("model", r.get("model_id", "")),
        r.get("family", r.get("_family", "")),
        r.get("length", r.get("_length", 0)),
        r.get("target", r.get("_target", "")),
        r.get("monitor_variant", "full_trace"),
        r.get("monitored_state", "unmonitored"),
    )


def aggregate_results(enriched: list[dict]) -> dict:
    """Group enriched results by (model, family, length, target, variant, monitored_state)
    and compute ConditionMetrics for each group. Returns a summary dict."""
    from collections import defaultdict
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in enriched:
        groups[_group_key(r)].append(r)

    summary = {}
    for gk, rs in groups.items():
        cm = ConditionMetrics.compute(rs)
        model, family, length, target, variant, mon_state = gk
        summary[gk] = {
            "model": model, "family": family, "length": length,
            "target": target, "monitor_variant": variant, "monitored_state": mon_state,
            "n_total": cm.n_total, "n_capturable": cm.n_capturable,
            "cir_mean": cm.cir.mean, "cir_ci": [cm.cir.ci_lo, cm.cir.ci_hi],
            "akr_mean": cm.akr.mean,
            "vr_mean": cm.vr.mean,
            "mfr_mean": cm.mfr.mean,
            "scheming_mean": cm.scheming.mean,
            "eemr_rate_product": cm.eemr.rate_product,
            "eemr_direct": cm.eemr.direct,
            "eemr_direct_ci": [cm.eemr_direct_ci.ci_lo, cm.eemr_direct_ci.ci_hi],
            "osm_mean": cm.osm.mean,
        }

    # CAS: for each (model, length, target, variant, mon_state) pair, compute CAS = EEMR(B) - EEMR(C).
    cas_table = {}
    for gk, data in summary.items():
        model, family, length, target, variant, mon_state = gk
        if family == "B":
            c_key = (model, "C", length, target, variant, mon_state)
            if c_key in summary:
                b_rs = groups[gk]
                c_rs = groups[c_key]
                cas_val = cas(b_rs, c_rs)
                cas_table[(model, length, target, variant, mon_state)] = cas_val

    # MCP: per (model, family, target, variant, mon_state), collapse point across lengths.
    mcp_table = {}
    from collections import defaultdict as dd
    mcp_groups: dict[tuple, dict[int, list[dict]]] = dd(dict)
    for gk, rs in groups.items():
        model, family, length, target, variant, mon_state = gk
        mcp_key = (model, family, target, variant, mon_state)
        mcp_groups[mcp_key][length] = rs

    for mcp_key, len_rs in mcp_groups.items():
        collapse = compute_mcp(len_rs)
        mcp_table[mcp_key] = collapse

    # Dissociation summary.
    diss = dissociation_report(enriched)

    return {
        "conditions": {str(k): v for k, v in summary.items()},
        "cas": {str(k): v for k, v in cas_table.items()},
        "mcp": {str(k): v for k, v in mcp_table.items()},
        "dissociation": {
            "n_total": diss.n_total,
            "n_dissociated": diss.n_dissociated,
            "rate": diss.rate,
        },
        "n_enriched": len(enriched),
    }


# ── Main entrypoint ────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Aggregate cached results (Layer 5)")
    parser.add_argument("--workspace", default=None, help="Override workspace root")
    args = parser.parse_args(argv)

    paths = resolve_root(mount=False, create=False)
    if args.workspace:
        from src.infra.paths import build_paths
        paths = build_paths(args.workspace, create=True)

    cache_root = paths.root
    runs_dir = paths.runs
    runs_dir.mkdir(parents=True, exist_ok=True)

    print(f"Workspace: {cache_root}")

    subject_results = load_subject_results(cache_root)
    judge_by_ck = load_judge_results(cache_root)
    baseline_by_ck = load_baseline_targets(cache_root)

    print(f"Loaded: {len(subject_results)} subject results, "
          f"{len(judge_by_ck)} judged conditions")

    if not subject_results:
        print("No subject results found. Run the experiment first.")
        return

    enriched = build_enriched_results(
        subject_results, judge_by_ck, baseline_by_ck, condition_meta={}
    )
    summary = aggregate_results(enriched)

    ts = time.strftime("%Y%m%dT%H%M%S")
    out_path = runs_dir / f"aggregate_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"Results written to: {out_path}")
    print(f"  {summary['n_enriched']} enriched results")
    print(f"  {len(summary['conditions'])} condition cells")
    print(f"  {len(summary['cas'])} CAS entries")
    print(f"  Dissociation rate: {summary['dissociation']['rate']:.3f}")


if __name__ == "__main__":
    main()
