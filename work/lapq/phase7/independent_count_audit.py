#!/usr/bin/env python3
"""Independent math/statistics recomputation; no engine or bootstrap execution."""
import hashlib
import json
import math
from pathlib import Path
import statistics
from collections import Counter, defaultdict

from release_source import source_pin_path

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def read_lines(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def main():
    summary = read(HERE / "replication_summary.json")
    plan = read(HERE / "experiment_plan.json")
    freeze = read(HERE / "frozen_protocol.json")
    execution = read(HERE / "results/execution.json")
    raw = read_lines(HERE / "results/raw-engine-output.jsonl")
    conditions = read_lines(HERE / "results/conditions.jsonl")
    references = read(HERE / "published_reference_curves.json")
    failures = []; checks = 0

    def check(condition, context):
        nonlocal checks
        checks += 1
        if not condition:
            failures.append({"context": context})

    def same(a, b, context):
        nonlocal checks
        checks += 1
        if not math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12):
            failures.append({"context": context, "independent": a, "reported": b})

    check(summary["complete"] is True and summary["audit"]["passed"] is True, "completed analysis status")
    check(execution["status"] == "passed" and not execution["failures"], "completed execution status")
    check(execution["freeze_sha256"] == sha(HERE / "frozen_protocol.json"), "frozen plan identity")
    check(execution["condition_file_sha256"] == sha(HERE / "results/conditions.jsonl"), "condition-file hash")
    check(execution["raw_engine_file_sha256"] == sha(HERE / "results/raw-engine-output.jsonl"), "raw-engine hash")
    for name, digest in freeze["source_sha256"].items():
        if name != "build/replication":
            check(sha(source_pin_path(name)) == digest, "source pin " + name)
    check(len(raw) == 11340 and len(conditions) == len(plan["conditions"]) == 3780, "raw coverage")
    check(len(summary["point_rows"]) == 378 and len(summary["curve_summaries"]) == 18, "summary coverage")
    records = defaultdict(dict); ids = set(); index = 0; insertions = 0
    for condition, spec in zip(conditions, plan["conditions"]):
        check(condition["passed"] is True and condition["metadata"]["spec"] == spec, "exact condition spec " + spec["condition_id"])
        check(len(condition["models"]) == 3, "three models " + spec["condition_id"])
        for model, result in zip(("offline", "online", "dirty"), condition["models"]):
            check(result == raw[index], "raw/condition equality " + str(index)); index += 1
            check(result["status"] == "ok" and result["sorted_ok"] is True and result["reference_only"] is False,
                  "validated result " + result["id"])
            check(result["id"] == spec["condition_id"] + "__" + model and result["model"] == model,
                  "result identity " + result["id"])
            check(result["id"] not in ids, "unique result " + result["id"]); ids.add(result["id"])
            check(result["n"] == result["height_count"] == spec["n"], "height and n " + result["id"])
            check(result["metric"] == "clean_key_comparisons_free_head", "metric " + result["id"])
            check(type(result["clean_comparisons"]) is int and result["clean_comparisons"] >= 0,
                  "integer clean count " + result["id"])
            check(type(result["dirty_comparisons"]) is int and result["dirty_comparisons"] >= 0,
                  "integer dirty count " + result["id"])
            check(model == "dirty" or result["dirty_comparisons"] == 0, "dirty counter scope " + result["id"])
            same(result["clean_comparisons"] / spec["n"], result["clean_comparisons_per_key"], "raw normalization " + result["id"])
            cell = (spec["regime"], spec["n"], model, spec["parameter_index"])
            check(spec["repetition"] not in records[cell], "unique repetition " + result["id"])
            records[cell][spec["repetition"]] = result
            insertions += spec["n"]
    expected = {(regime, n, model, parameter) for regime in ("class", "decay") for n in (1000, 10000, 100000)
                for model in ("offline", "online", "dirty") for parameter in range(21)}
    check(set(records) == expected and all(set(rows) == set(range(30)) for rows in records.values()), "complete 378 by30 grid")
    check(insertions == 419580000, "ordinal insertion coverage")
    point_map = {(r["regime"], r["n"], r["model"], r["parameter_index"]): r for r in summary["point_rows"]}
    reference_map = {(r["regime"], r["n"], r["model"], r["parameter_index"]): r for r in references["reference_rows"]}
    check(set(point_map) == set(reference_map) == expected, "point/reference grid")
    curve_keys = {cell[:3] for cell in expected}
    for field in ("curve_summaries", "endpoint_contrasts"):
        check(len(summary[field]) == 18 and {(r["regime"], r["n"], r["model"]) for r in summary[field]} == curve_keys,
              "complete unique curve coverage " + field)
    heap = {}
    for n in (1000, 10000, 100000):
        # Use direct floor(log2) as written in the released formula; analysis uses
        # integer bit_length. The summation below uses math.fsum independently.
        heap[n] = math.fsum(math.log2(math.floor(math.log2(k + 1))) + math.floor(math.log2(k + 1)) - 1
                            for k in range(1, n)) / n
        same(heap[n], summary["analytic_bh_per_key"][str(n)], "analytic BH " + str(n))
    means = {}; errors = {}; ratios = {}; computed_points = []
    for cell in sorted(expected):
        regime, n, model, parameter = cell
        values = [records[cell][rep]["clean_comparisons"] / n for rep in range(30)]
        dirty = [records[cell][rep]["dirty_comparisons"] / n for rep in range(30)]
        point = point_map[cell]; original = reference_map[cell]["mean_clean_per_key"]
        mean = math.fsum(values) / len(values); means[cell] = mean
        same(mean, point["mean_clean_per_key"], f"cell mean {cell}")
        same(statistics.stdev(values), point["sample_sd"], f"cell sample SD {cell}")
        same(statistics.pstdev(values), point["population_sd"], f"cell population SD {cell}")
        same(math.fsum(dirty) / len(dirty), point["mean_dirty_per_key"], f"dirty mean {cell}")
        same(statistics.stdev(dirty), point["dirty_sample_sd"], f"dirty sample SD {cell}")
        same(statistics.pstdev(dirty), point["dirty_population_sd"], f"dirty population SD {cell}")
        difference = mean - original; relative = abs(difference) / original
        errors[cell] = (difference, relative); ratios[cell] = mean / heap[n]
        same(original, point["reference_mean_clean_per_key"], f"reference mean {cell}")
        same(difference, point["signed_error"], f"signed error {cell}")
        same(abs(difference), point["absolute_error"], f"absolute error {cell}")
        same(difference / original, point["signed_relative_error"], f"relative error {cell}")
        same(relative, point["absolute_relative_error"], f"absolute relative error {cell}")
        same(ratios[cell], point["ratio_to_analytic_bh"], f"BH ratio {cell}")
        computed_points.append(dict(regime=regime, n=n, model=model, parameter_index=parameter,
                                    mean=mean, sample_sd=statistics.stdev(values), population_sd=statistics.pstdev(values),
                                    absolute_relative_error=relative, ratio_to_bh=ratios[cell]))
    curves = []
    for curve in summary["curve_summaries"]:
        base = (curve["regime"], curve["n"], curve["model"])
        cells = [base + (parameter,) for parameter in range(21)]
        error = [errors[cell][0] for cell in cells]; relative = [errors[cell][1] for cell in cells]
        original = [reference_map[cell]["mean_clean_per_key"] for cell in cells]
        rmse = math.sqrt(math.fsum(value * value for value in error) / 21)
        norm = rmse / (math.fsum(original) / 21)
        same(statistics.median(relative), curve["median_absolute_relative_error"], f"curve median {base}")
        same(max(relative), curve["maximum_absolute_relative_error"], f"curve maximum {base}")
        same(rmse, curve["rmse"], f"curve RMSE {base}")
        same(norm, curve["normalized_rmse"], f"curve normalized RMSE {base}")
        check(relative.index(max(relative)) == curve["maximum_error_parameter_index"], f"curve max index {base}")
        curves.append(dict(regime=base[0], n=base[1], model=base[2], median_absolute_relative_error=statistics.median(relative),
                           maximum_absolute_relative_error=max(relative), normalized_rmse=norm))
    directions = []
    for endpoint in summary["endpoint_contrasts"]:
        base = (endpoint["regime"], endpoint["n"], endpoint["model"])
        first, last = means[base + (0,)], means[base + (20,)]
        same(first, endpoint["first_mean"], f"endpoint first {base}")
        same(last, endpoint["last_mean"], f"endpoint last {base}")
        same(last - first, endpoint["last_minus_first"], f"endpoint change {base}")
        same(last / first, endpoint["ratio_last_to_first"], f"endpoint ratio {base}")
        observed = "decrease" if last < first else "increase" if last > first else "equal"
        expected_direction = "decrease" if base[0] == "class" else "increase"
        check(observed == expected_direction == endpoint["expected_endpoint_direction"], f"endpoint direction {base}")
        lo, hi = endpoint["difference_ci95_low"], endpoint["difference_ci95_high"]
        derived_assessment = ("supported" if (hi < 0 if base[0] == "class" else lo > 0)
                              else "unsupported" if (lo > 0 if base[0] == "class" else hi < 0) else "indeterminate")
        check(derived_assessment == endpoint["assessment"], f"endpoint classification from supplied CI {base}")
        directions.append(dict(regime=base[0], n=base[1], model=base[2], observed=observed,
                               first=first, last=last, change=last-first))
    above = [dict(regime=c[0], n=c[1], model=c[2], parameter_index=c[3]) for c, ratio in sorted(ratios.items()) if ratio > 1]
    below = sum(ratio < 1 for ratio in ratios.values())
    check(len(above) == 9 and below == 369, "BH369below9above")
    check(all(row["regime"] == "class" and row["parameter_index"] == 0 for row in above), "BH above cells exactly class c0")
    classification = Counter("below" if r["ratio_to_bh_ci95_high"] < 1 else "above" if r["ratio_to_bh_ci95_low"] > 1 else "indeterminate"
                             for r in summary["point_rows"])
    check(dict(classification) == summary["claims"]["bh_pointwise_counts"], "BH CI classification using supplied bounds")
    main_curves = [c for c in curves if c["n"] != 1000]
    anchor_curves = [c for c in curves if c["n"] == 1000]
    numeric_projection = {name: summary[name] for name in ("audit", "point_rows", "curve_summaries", "endpoint_contrasts",
                           "paired_model_ratios", "bh_crossovers", "analytic_bh_per_key", "claims")}
    output = dict(status="passed" if not failures else "failed", independent_source_sha256=sha(__file__), checks=checks,
                  failures=failures, raw_conditions=len(conditions), raw_outputs=len(raw), cells=len(records),
                  ordinal_insertions=insertions, curve_count=len(curves), endpoint_count=len(directions),
                  input_sha256={name: sha(HERE/name) for name in ("frozen_protocol.json", "experiment_plan.json",
                                 "results/execution.json", "results/conditions.jsonl", "results/raw-engine-output.jsonl",
                                 "published_reference_curves.json")},
                  audited_numeric_summary_sha256=hashlib.sha256(json.dumps(numeric_projection,sort_keys=True,separators=(",", ":")).encode()).hexdigest(),
                  summary_hash_scope="Calculated fields only; manuscript prose may be appended separately without changing this audit projection.",
                  independence="Uses math.fsum, statistics.mean-equivalent sum/n, statistics.stdev/pstdev directly on raw integer counts. Does not import or call analyze_replication.py.",
                  bootstrap_scope="Bootstrap sampling is not independently repeated. Endpoint/BH classifications are checked against supplied interval bounds; raw endpoint directions are independently recomputed.",
                  analytic_bh_per_key=heap, bh_mean_below_count=below, bh_mean_above_count=len(above), bh_above_cells=above,
                  computed_curves=curves, computed_endpoints=directions,
                  main_median_pointwise_relative_error_range=[min(c["median_absolute_relative_error"] for c in main_curves),
                                                            max(c["median_absolute_relative_error"] for c in main_curves)],
                  main_maximum_pointwise_relative_error=max(c["maximum_absolute_relative_error"] for c in main_curves),
                  n1000_maximum_pointwise_relative_error=max(c["maximum_absolute_relative_error"] for c in anchor_curves),
                  numerical_citation_scope="PDF-derived means are rounded plotted coordinates, not original raw measurements; released n1000 standard deviations are not standard errors. Paper states nominal30runs; saved aggregates lack raw trials/effective-count verification.")
    (HERE / "independent_count_audit.json").write_text(json.dumps(output, indent=2, allow_nan=False) + "\n")
    low, high = output["main_median_pointwise_relative_error_range"]
    text = (f"Independent count audit: {output['status'].upper()} ({checks:,} checks).\n"
            f"Audited 3,780 conditions, 11,340 raw method outputs, 419,580,000 ordinal insertions, 378 cells and 18 curves.\n"
            "All clean/dirty means, sample/population SDs, point errors, curve normalized RMSEs and endpoint changes agree.\n"
            f"Main-size curve median pointwise relative errors: {100*low:.6f}% to {100*high:.6f}%; maximum pointwise error {100*output['main_maximum_pointwise_relative_error']:.6f}%.\n"
            f"n = 1,000 maximum pointwise relative error: {100*output['n1000_maximum_pointwise_relative_error']:.6f}%.\n"
            "BH: 369 cell means below and 9 above; all 9 above are class parameter 0. All 18 endpoint directions match their scoped class/decay direction.\n"
            "Bootstrap draws were not repeated. This verifies calculated summaries, not original trial-level agreement or runtime superiority.\n"
            "The original paper states 30 nominal runs; raw original trials and effective count for saved aggregate files remain unavailable.\n")
    (HERE / "independent_count_audit.txt").write_text(text)
    print(json.dumps({key: output[key] for key in ("status", "checks", "cells", "curve_count", "endpoint_count", "bh_mean_below_count", "bh_mean_above_count")}))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
