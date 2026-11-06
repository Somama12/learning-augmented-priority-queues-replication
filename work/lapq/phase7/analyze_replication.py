#!/usr/bin/env python3
"""Audit and summarize the frozen comparison-count replication, after completion.

This program does not regenerate inputs, execute the engine, or infer unknown
original trial counts. It refuses a running, failed, incomplete or altered run.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys

import numpy as np

from release_source import source_pin_path, verify_release_source

HERE = Path(__file__).resolve().parent
BOOTSTRAP_SEED = 2026110608
BOOTSTRAP_RESAMPLES = 5000
MODELS = ("offline", "online", "dirty")
HISTORICAL_EXECUTABLE = "build/replication"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def rng(label):
    encoded = f"{BOOTSTRAP_SEED}:{label}".encode()
    seed = int.from_bytes(hashlib.sha256(encoded).digest()[:16], "little")
    return np.random.Generator(np.random.PCG64(seed))


def bootstrap_mean(values, label):
    values = np.asarray(values, dtype=np.float64)
    indices = rng(label).integers(0, len(values), size=(BOOTSTRAP_RESAMPLES, len(values)))
    means = values[indices].mean(axis=1)
    return [float(x) for x in np.quantile(means, [.025, .975], method="linear")]


def describe(values, label):
    values = np.asarray(values, dtype=np.float64)
    lo, hi = bootstrap_mean(values, label)
    return dict(mean=float(values.mean()), sample_sd=float(values.std(ddof=1)),
                population_sd=float(values.std(ddof=0)), ci95_low=lo, ci95_high=hi)


def endpoint_difference(first, last, label):
    # Parameter-specific seeds are independent. Repetition numbers do not create
    # pairs between different parameter values; resample each endpoint separately.
    a = np.asarray(first, dtype=np.float64); b = np.asarray(last, dtype=np.float64)
    ai = rng(label + ":first").integers(0, len(a), size=(BOOTSTRAP_RESAMPLES, len(a)))
    bi = rng(label + ":last").integers(0, len(b), size=(BOOTSTRAP_RESAMPLES, len(b)))
    delta = b[bi].mean(axis=1) - a[ai].mean(axis=1)
    lo, hi = np.quantile(delta, [.025, .975], method="linear")
    return float(b.mean() - a.mean()), float(lo), float(hi)


def bh_reference(n):
    # Exact released analytic expression; this is not a measured heap.
    total = 0.0
    for k in range(1, n):
        depth = (k + 1).bit_length() - 1
        total += math.log2(depth) + depth - 1
    return total / n


def key(row):
    return row["regime"], row["n"], row["model"], row["parameter_index"]


def audit_inputs(results):
    execution = read_json(results / "execution.json")
    require(execution.get("status") == "passed", "Execution must finish with status passed before analysis")
    freeze = read_json(HERE / "frozen_protocol.json")
    plan = read_json(HERE / "experiment_plan.json")
    references = read_json(HERE / "published_reference_curves.json")
    require(freeze["status"] == "frozen_before_primary_execution", "Protocol was not frozen")
    require(execution["freeze_sha256"] == sha(HERE / "frozen_protocol.json"), "Frozen protocol hash mismatch")
    require(not execution.get("failures"), "Execution contains failures")
    for name, expected in freeze["source_sha256"].items():
        # Public analysis artifacts do not ship a foreign executable. The frozen
        # run's own source guards checked this binary during execution; analysis
        # retains that historical identity without loading or executing it.
        if name != HISTORICAL_EXECUTABLE:
            require(sha(source_pin_path(name)) == expected, "Frozen source hash mismatch: " + name)
    require(re.fullmatch(r"[0-9a-f]{64}", freeze["source_sha256"][HISTORICAL_EXECUTABLE]), "Malformed historical binary hash")
    require(plan["condition_count"] == freeze["condition_count"] == 3780, "Unexpected planned condition count")
    require(plan["method_run_count"] == freeze["method_run_count"] == 11340, "Unexpected planned method count")
    require(plan["models"] == list(MODELS), "Model order changed")
    require(plan["sizes"] == [1000, 10000, 100000], "Target sizes changed")
    require(plan["parameter_levels"] == 21 and plan["repetitions_per_condition"] == 30, "Grid changed")
    require(execution["planned_conditions"] == execution["completed_conditions"] == 3780, "Incomplete execution")
    require(execution["planned_method_runs"] == 11340, "Execution method count mismatch")
    require(execution["condition_file_sha256"] == sha(results / "conditions.jsonl"), "Condition file hash mismatch")
    require(execution["raw_engine_file_sha256"] == sha(results / "raw-engine-output.jsonl"), "Raw engine file hash mismatch")
    conditions = read_jsonl(results / "conditions.jsonl")
    raw = read_jsonl(results / "raw-engine-output.jsonl")
    require(len(conditions) == 3780 and len(raw) == 11340, "Raw result coverage mismatch")
    require(len(plan["conditions"]) == 3780, "Plan length mismatch")
    identifiers = set(); cells = defaultdict(dict); input_hashes = []
    generation_algorithms = Counter(); raw_index = 0
    for record, spec in zip(conditions, plan["conditions"]):
        require(record.get("passed") is True, "Condition validation failed")
        metadata = record["metadata"]
        require(metadata["spec"] == spec, "Executed spec differs from frozen plan")
        cid = spec["condition_id"]
        require(cid not in identifiers, "Duplicate condition ID")
        identifiers.add(cid)
        require(spec["regime"] in ("class", "decay") and 0 <= spec["parameter_index"] <= 20 and 0 <= spec["repetition"] < 30,
                "Out-of-grid condition")
        require(metadata["numpy_version"] == plan["numpy_version"], "Input generator NumPy version changed")
        require(metadata["height_count"] == spec["n"] and 1 <= metadata["maximum_height"] <= 1024, "Invalid height metadata")
        require(set(metadata["hashes"]) == {"predictions", "order", "heights"}, "Input hash names differ")
        require(all(re.fullmatch(r"[0-9a-f]{64}", value) for value in metadata["hashes"].values()), "Malformed input hash")
        input_hashes.append({"condition_id": cid, **metadata["hashes"]})
        generation_algorithms[spec["generation_algorithm"]] += 1
        require(len(record["models"]) == 3, "Model coverage mismatch")
        for model, out in zip(MODELS, record["models"]):
            require(out == raw[raw_index], "Condition record differs from raw engine output")
            raw_index += 1
            require(out["status"] == "ok" and out["id"] == cid + "__" + model and out["model"] == model,
                    "Output identity/status mismatch")
            require(out["n"] == spec["n"] and out["height_count"] == spec["n"] and out["sorted_ok"] is True,
                    "Sort or height validation failure")
            require(out["reference_only"] is False and out["metric"] == "clean_key_comparisons_free_head", "Metric changed")
            for count in (out["clean_comparisons"], out["dirty_comparisons"]):
                require(type(count) is int and count >= 0, "Noninteger or negative comparison count")
            require(math.isclose(out["clean_comparisons_per_key"], out["clean_comparisons"] / spec["n"], rel_tol=1e-15, abs_tol=1e-15),
                    "Per-key normalization mismatch")
            require(model == "dirty" or out["dirty_comparisons"] == 0, "Dirty comparison in clean-only model")
            cell = (spec["regime"], spec["n"], model, spec["parameter_index"])
            require(spec["repetition"] not in cells[cell], "Duplicate cell repetition")
            cells[cell][spec["repetition"]] = {"clean": out["clean_comparisons"] / spec["n"],
                                              "dirty": out["dirty_comparisons"] / spec["n"], "spec": spec}
    expected_keys = {(regime, n, model, index) for regime in ("class", "decay")
                     for n in plan["sizes"] for model in MODELS for index in range(21)}
    require(set(cells) == expected_keys, "Cell coverage differs from complete grid")
    require(all(set(values) == set(range(30)) for values in cells.values()), "Cell repetitions are incomplete")
    ref = {key(row): row for row in references["reference_rows"]}
    require(references["passed"] is True and len(references["reference_rows"]) == 378 and set(ref) == expected_keys,
            "Published reference coverage or validation mismatch")
    require(sha(HERE / "sources/original-paper.pdf") == references["source_pdf_sha256"], "Reference PDF hash mismatch")
    kind_counts = Counter(row["reference_kind"] for row in ref.values())
    require(sum(count for kind, count in kind_counts.items() if kind.startswith("PDF")) == 252, "PDF reference count mismatch")
    require(sum(count for kind, count in kind_counts.items() if kind.startswith("Released")) == 126, "Aggregate reference count mismatch")
    # Independently check all released n1000 aggregate means/std, without inferring
    # their original number of repetitions or treating reported std as an SE.
    for regime in ("class", "decay"):
        original = read_json(HERE / f"sources/data_{regime}_1000.json")
        for model, prefix in (("offline", "SL"), ("online", "OSL"), ("dirty", "DC")):
            name = f"{prefix}_{regime}_1000"
            for index in range(21):
                row = ref[(regime, 1000, model, index)]
                require(row["mean_clean_per_key"] == original["mean"][name][index], "Released mean mismatch")
                require(row["std_clean_per_key"] == original["std"][name][index], "Released std mismatch")
    input_ledger_hash = hashlib.sha256(json.dumps(input_hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    ordinal_insertions = sum(spec["n"] * len(MODELS) for spec in plan["conditions"])
    require(ordinal_insertions == 419580000, "Ordinal insertion coverage mismatch")
    audit = dict(passed=True, conditions=3780, method_outputs=11340, cells=378, repetitions_per_cell=30,
                 ordinal_insertions=ordinal_insertions,
                 sorted_and_height_checks_passed=11340, raw_and_condition_outputs_identical=True,
                 complete_frozen_grid=True, source_hashes_match=True,
                 historical_binary_sha256=freeze["source_sha256"][HISTORICAL_EXECUTABLE],
                 binary_validation_scope="Original executable SHA256 retained as historical provenance; frozen runner checked it during execution. Analysis neither requires nor executes that binary. All nonbinary frozen source/evidence pins are verified.",
                 input_hash_ledger_sha256=input_ledger_hash,
                 input_hash_scope="All recorded SHA256 formats/specs/seeds audited; arrays are not retained or independently regenerated by this analysis.",
                 generation_algorithms=dict(generation_algorithms), reference_kind_counts=dict(kind_counts),
                 released_aggregate_fields_independently_checked=252,
                 original_standard_errors_inferred=False)
    return execution, freeze, plan, references, cells, ref, audit


def calculate(cells, ref, plan):
    heap = {n: bh_reference(n) for n in plan["sizes"]}
    points = []; arrays = {}; dirty_arrays = {}
    for cell in sorted(cells):
        regime, n, model, index = cell
        ordered = [cells[cell][rep] for rep in range(30)]
        clean = np.array([value["clean"] for value in ordered]); arrays[cell] = clean
        dirty = np.array([value["dirty"] for value in ordered]); dirty_arrays[cell] = dirty
        stats = describe(clean, repr(cell) + ":clean")
        dirty_stats = describe(dirty, repr(cell) + ":dirty")
        spec = ordered[0]["spec"]; original = ref[cell]
        reference_mean = original["mean_clean_per_key"]
        require(reference_mean > 0, "Relative error requires positive published reference")
        difference = stats["mean"] - reference_mean
        ratio = stats["mean"] / heap[n]
        ratio_lo, ratio_hi = stats["ci95_low"] / heap[n], stats["ci95_high"] / heap[n]
        row = dict(regime=regime, n=n, model=model, parameter_index=index,
                   parameter_name=spec["parameter_name"], parameter=spec["parameter"],
                   effective_parameter=spec["effective_parameter"], repetitions=30,
                   mean_clean_per_key=stats["mean"], sample_sd=stats["sample_sd"], population_sd=stats["population_sd"],
                   ci95_low=stats["ci95_low"], ci95_high=stats["ci95_high"],
                   mean_dirty_per_key=dirty_stats["mean"], dirty_sample_sd=dirty_stats["sample_sd"],
                   dirty_population_sd=dirty_stats["population_sd"], dirty_ci95_low=dirty_stats["ci95_low"],
                   dirty_ci95_high=dirty_stats["ci95_high"], reference_mean_clean_per_key=reference_mean,
                   reference_std_as_released=original.get("std_clean_per_key"), reference_kind=original["reference_kind"],
                   source_figure=original.get("source_figure"), source_page_1based=original.get("source_page_1based"),
                   signed_error=difference, absolute_error=abs(difference), signed_relative_error=difference / reference_mean,
                   absolute_relative_error=abs(difference) / reference_mean,
                   analytic_bh_per_key=heap[n], ratio_to_analytic_bh=ratio,
                   ratio_to_bh_ci95_low=ratio_lo, ratio_to_bh_ci95_high=ratio_hi,
                   bh_pointwise_classification="below" if ratio_hi < 1 else "above" if ratio_lo > 1 else "indeterminate")
        points.append(row)
    curves = []; endpoints = []; crossovers = []
    for regime in ("class", "decay"):
        for n in plan["sizes"]:
            for model in MODELS:
                rows = sorted([row for row in points if (row["regime"], row["n"], row["model"]) == (regime, n, model)],
                              key=lambda row: row["parameter_index"])
                relative = np.array([row["absolute_relative_error"] for row in rows])
                errors = np.array([row["signed_error"] for row in rows])
                original = np.array([row["reference_mean_clean_per_key"] for row in rows])
                maximum = int(relative.argmax())
                curves.append(dict(regime=regime, n=n, model=model, point_count=21,
                                   reference_kind=rows[0]["reference_kind"],
                                   median_absolute_relative_error=float(np.median(relative)),
                                   maximum_absolute_relative_error=float(relative.max()),
                                   maximum_error_parameter_index=rows[maximum]["parameter_index"],
                                   rmse=float(np.sqrt(np.mean(errors ** 2))),
                                   normalized_rmse=float(np.sqrt(np.mean(errors ** 2)) / original.mean()),
                                   normalization="RMSE / arithmetic mean of the 21 reference means",
                                   mean_reference_curve=float(original.mean())))
                first = arrays[(regime, n, model, 0)]; last = arrays[(regime, n, model, 20)]
                change, lo, hi = endpoint_difference(first, last, repr((regime, n, model)) + ":endpoint")
                direction = "decrease" if regime == "class" else "increase"
                assessment = ("supported" if (hi < 0 if regime == "class" else lo > 0)
                              else "unsupported" if (lo > 0 if regime == "class" else hi < 0) else "indeterminate")
                endpoints.append(dict(regime=regime, n=n, model=model, first_parameter=rows[0]["parameter"],
                                      last_parameter=rows[-1]["parameter"], first_mean=float(first.mean()), last_mean=float(last.mean()),
                                      last_minus_first=change, difference_ci95_low=lo, difference_ci95_high=hi,
                                      ratio_last_to_first=float(last.mean() / first.mean()),
                                      expected_endpoint_direction=direction, assessment=assessment,
                                      scope="Endpoint direction only; independent endpoint resampling; no adjacent monotonicity or universal claim"))
                crossings = []
                exact = []
                for row in rows:
                    if row["ratio_to_analytic_bh"] == 1: exact.append(row["parameter_index"])
                for left, right in zip(rows, rows[1:]):
                    a = left["ratio_to_analytic_bh"] - 1; b = right["ratio_to_analytic_bh"] - 1
                    if a * b < 0:
                        crossings.append(dict(left_parameter_index=left["parameter_index"], right_parameter_index=right["parameter_index"],
                                              left_parameter=left["parameter"], right_parameter=right["parameter"],
                                              left_ratio=left["ratio_to_analytic_bh"], right_ratio=right["ratio_to_analytic_bh"]))
                crossovers.append(dict(regime=regime, n=n, model=model, mean_crossing_brackets=crossings,
                                       exact_equal_mean_parameter_indices=exact,
                                       mean_below_parameter_indices=[r["parameter_index"] for r in rows if r["ratio_to_analytic_bh"] < 1],
                                       mean_above_parameter_indices=[r["parameter_index"] for r in rows if r["ratio_to_analytic_bh"] > 1],
                                       ci_below_parameter_indices=[r["parameter_index"] for r in rows if r["bh_pointwise_classification"] == "below"],
                                       ci_above_parameter_indices=[r["parameter_index"] for r in rows if r["bh_pointwise_classification"] == "above"],
                                       ci_indeterminate_parameter_indices=[r["parameter_index"] for r in rows if r["bh_pointwise_classification"] == "indeterminate"],
                                       scope="Observed full grid; no interpolated threshold and no assumed monotonicity"))
    paired = []
    for regime in ("class", "decay"):
        for n in plan["sizes"]:
            for index in range(21):
                for numerator in ("online", "dirty"):
                    denominator = arrays[(regime, n, "offline", index)]
                    values = arrays[(regime, n, numerator, index)]
                    require(bool(np.all(denominator > 0)), "Zero denominator in paired model ratio")
                    ratios = values / denominator
                    stats = describe(ratios, repr((regime, n, index, numerator)) + ":paired-ratio")
                    paired.append(dict(regime=regime, n=n, parameter_index=index, numerator=numerator, denominator="offline",
                                       paired_repetitions=30, mean_paired_ratio=stats["mean"], paired_ratio_sample_sd=stats["sample_sd"],
                                       paired_ratio_population_sd=stats["population_sd"], ci95_low=stats["ci95_low"], ci95_high=stats["ci95_high"],
                                       ratio_of_means=float(values.mean() / denominator.mean()),
                                       difference_of_means=float((values - denominator).mean()),
                                       scope="Descriptive common-condition ratio; no equivalence margin or universal competitiveness claim"))
    return points, curves, endpoints, crossovers, paired, heap


def write_csv(path, rows):
    if not rows: return
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=HERE / "results")
    parser.add_argument("--output", type=Path, default=HERE / "replication_summary.json")
    args = parser.parse_args()
    # Refuse early without writing an apparent completed summary during execution.
    require(read_json(args.results / "execution.json").get("status") == "passed", "Primary execution is not passed; analysis not started")
    execution, freeze, plan, references, cells, ref, audit = audit_inputs(args.results)
    points, curves, endpoints, crossovers, paired, heap = calculate(cells, ref, plan)
    require(execution["condition_file_sha256"] == sha(args.results / "conditions.jsonl"), "Conditions changed during analysis")
    require(execution["raw_engine_file_sha256"] == sha(args.results / "raw-engine-output.jsonl"), "Raw output changed during analysis")
    for name, expected in freeze["source_sha256"].items():
        if name != HISTORICAL_EXECUTABLE:
            require(sha(source_pin_path(name)) == expected, "Frozen source changed during analysis: " + name)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tables = args.output.parent / "tables"; tables.mkdir(exist_ok=True)
    table_rows = {"replication_points.csv": points, "replication_curves.csv": curves,
                  "replication_endpoints.csv": endpoints, "replication_paired_ratios.csv": paired}
    for name, rows in table_rows.items(): write_csv(tables / name, rows)
    source_files = [HERE / "frozen_protocol.json", HERE / "experiment_plan.json", HERE / "published_reference_curves.json",
                    HERE / "sources/original-paper.pdf", HERE / "sources/data_class_1000.json", HERE / "sources/data_decay_1000.json",
                    args.results / "execution.json", args.results / "conditions.jsonl", args.results / "raw-engine-output.jsonl"]
    output = dict(complete=True, audit=audit, created_utc=datetime.now(timezone.utc).isoformat(),
                  analysis_source_sha256=sha(__file__), python=sys.version, numpy_version=np.__version__,
                  release_source_revision=verify_release_source(),
                  source_sha256={str(path.relative_to(HERE)) if path.is_relative_to(HERE) else str(path): sha(path) for path in source_files},
                  analysis_method=dict(bootstrap_resamples=BOOTSTRAP_RESAMPLES, master_seed=BOOTSTRAP_SEED,
                                       seed_derivation="First16 SHA256 bytes of seed:label as little-endian integer; NumPy PCG64",
                                       ci="Pointwise percentile 95% bootstrap of 30 trials, linear quantiles; no simultaneous correction",
                                       paired_models="Resample paired within-condition ratios across the 30 common-input trials",
                                       endpoints="Separate bootstrap samples for independently seeded first/last parameter trials",
                                       curve_error="Absolute relative mean error at all21points; normalized RMSE divides by mean reference curve",
                                       original_uncertainty="Original std retained as reported; no original SE, CI or trial count inferred"),
                  point_rows=points, curve_summaries=curves, endpoint_contrasts=endpoints,
                  paired_model_ratios=paired, bh_crossovers=crossovers,
                  analytic_bh_per_key={str(n): value for n, value in heap.items()},
                  claims=dict(endpoint_assessments=endpoints,
                              endpoint_assessment_counts=dict(Counter(row["assessment"] for row in endpoints)),
                              bh_pointwise_counts=dict(Counter(row["bh_pointwise_classification"] for row in points)),
                              paired_endpoint_ratios=[row for row in paired if row["parameter_index"] in (0, 20)],
                              scope="Claim-specific descriptive evidence; no single automatic replication-success score, equivalence declaration, or runtime claim"),
                  reference_limits=references["limits"], reference_validation=references["validation"],
                  published_heap_discrepancy=freeze["publication_reference_note"],
                  table_sha256={"tables/" + name: sha(tables / name) for name in table_rows},
                  execution_status=execution["status"])
    args.output.write_text(json.dumps(output, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"complete": True, "audit_passed": True, "points": len(points), "curves": len(curves),
                      "endpoint_contrasts": len(endpoints), "paired_ratio_cells": len(paired), "summary": str(args.output)}))


if __name__ == "__main__":
    main()
