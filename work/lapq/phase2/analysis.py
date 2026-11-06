#!/usr/bin/env python3
"""Summarize paired source-level timing; repeats are not independent workloads."""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import random
import statistics as st


def quantile(values, probability):
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered)-1) * probability
    lower = int(position)
    return ordered[lower] + (position-lower) * (ordered[min(lower+1, len(ordered)-1)]-ordered[lower])


def cluster_interval(units, field, draws=2000, seed=80931):
    clusters = defaultdict(list)
    for unit in units:
        clusters[unit["train_source"]].append(unit)
    if len(clusters) < 2:
        return None
    rng = random.Random(seed)
    names = sorted(clusters)
    estimates = []
    for _ in range(draws):
        values = []
        for name in rng.choices(names, k=len(names)):
            sample = clusters[name]
            # Preserve the frozen near/far composition within every train cluster.
            # In the current first-near/first-far design each stratum has one query.
            strata = defaultdict(list)
            for unit in sample:
                strata[unit["query_group"]].append(unit)
            for group in sorted(strata):
                stratum = strata[group]
                values.extend(unit[field] for unit in rng.choices(stratum, k=len(stratum)))
        estimates.append(st.median(values))
    return {"lower": quantile(estimates, .025), "upper": quantile(estimates, .975),
            "method": "Exploratory hierarchical percentile bootstrap: training-source clusters, then held-out query sources within near/far strata; repetitions collapsed first.",
            "resamples": draws, "training_source_clusters": len(clusters),
            "limitations": "Conditions on this graph and sampled training/query sources; not uncertainty across independent graphs. Few training clusters limit reliability."}


def advisor_of(method):
    return method.rsplit("_", 1)[-1] if method.rsplit("_", 1)[-1] in ("exact", "linear", "hist64") else None


def analyze(paths, bootstrap_draws=2000, include_pilot=False):
    units = defaultdict(list)
    ablation_units = defaultdict(list)
    ablation_pairs = [("bitmap_exact", "original_exact"), ("bitmap_linear", "original_linear"),
                      ("bitmap_hist64", "original_hist64"), ("veb_linear", "original_linear"),
                      ("compact_linear", "bitmap_linear"), ("heapq_shared", "heapq_native")]
    memory, failures, all_rows, training_records, included_documents = [], [], [], [], []
    block_ids = set()
    for path in paths:
        document = json.loads(path.read_text())
        if document.get("spec", {}).get("stage") == "feasibility_pilot" and not include_pilot:
            continue
        included_documents.append((path, document))
        if document.get("kind") == "memory":
            memory.append({"file": str(path), **document})
            if document.get("status") != "ok":
                failures.append({"file": str(path), "reason": document.get("status"), "spec": document.get("spec")})
            continue
        if document.get("kind") != "timing_block":
            if document.get("status") in ("worker_error", "timeout"):
                failures.append({"file": str(path), "reason": document["status"], "spec": document.get("spec")})
            continue
        spec = document["spec"]
        identity = (spec["workload"], spec["train_source"], spec["query_source"], spec["repeat"])
        if identity in block_ids:
            raise ValueError("Duplicate paired block across inputs: " + repr(identity))
        block_ids.add(identity)
        successful = {r["method"]: r for r in document["records"] if r["status"] == "ok"}
        for row in document["records"]:
            all_rows.append({"spec": spec, **row})
            if row["status"] != "ok":
                failures.append({"file": str(path), "method": row["method"], "reason": row.get("error", row["status"]), "spec": spec})
        for numerator, denominator in ablation_pairs:
            if numerator in successful and denominator in successful:
                a, b = successful[numerator], successful[denominator]
                key = (spec["workload"], spec["train_source"], spec["query_source"], spec["query_group"], numerator, denominator)
                ablation_units[key].append({"repeat": spec["repeat"], "queue_seed": spec["queue_seed"],
                    "wall_ratio": a["query_wall_ns"]/b["query_wall_ns"],
                    "cpu_ratio": a["query_cpu_ns"]/b["query_cpu_ns"],
                    "numerator_wall_ns": a["query_wall_ns"], "denominator_wall_ns": b["query_wall_ns"],
                    "numerator_cpu_ns": a["query_cpu_ns"], "denominator_cpu_ns": b["query_cpu_ns"]})
        baseline = successful.get("heapq_native")
        if baseline is None:
            failures.append({"file": str(path), "reason": "No successful native heap baseline; paired ratios unavailable.", "spec": spec})
            continue
        training = document.get("training", {})
        training_records.append({"spec": spec, "training": training})
        for method, row in successful.items():
            advisor = advisor_of(method)
            build = training.get("advisor_builds", {}).get(advisor, {})
            collection = training.get("collecting_dijkstra", {}) if advisor else {}
            record = {"repeat": spec["repeat"], "queue_seed": spec["queue_seed"],
                      "wall_ratio": row["query_wall_ns"]/baseline["query_wall_ns"],
                      "cpu_ratio": row["query_cpu_ns"]/baseline["query_cpu_ns"],
                      "query_wall_ns": row["query_wall_ns"], "query_cpu_ns": row["query_cpu_ns"],
                      "heap_wall_ns": baseline["query_wall_ns"], "heap_cpu_ns": baseline["query_cpu_ns"],
                      "paired_wall_saving_ns": baseline["query_wall_ns"]-row["query_wall_ns"],
                      "paired_cpu_saving_ns": baseline["query_cpu_ns"]-row["query_cpu_ns"],
                      "wall_cpu_ratio": row["wall_cpu_ratio"],
                      "forced_cleanup_wall_ns": row.get("forced_cleanup_wall_ns", 0),
                      "cold_training_plus_build_wall_ns": collection.get("wall_ns", 0)+build.get("wall_ns", 0),
                      "cold_training_plus_build_cpu_ns": collection.get("cpu_ns", 0)+build.get("cpu_ns", 0)}
            key = (spec["workload"], spec["train_source"], spec["query_source"], spec["query_group"], method)
            units[key].append(record)
    source_rows = []
    for (workload, train, query, group, method), repeats in sorted(units.items()):
        row = {"workload": workload, "train_source": train, "query_source": query,
               "query_group": group, "method": method, "repeat_count": len(repeats),
               "wall_ratio_median": st.median(r["wall_ratio"] for r in repeats),
               "cpu_ratio_median": st.median(r["cpu_ratio"] for r in repeats),
               "query_wall_ns_median": st.median(r["query_wall_ns"] for r in repeats),
               "query_cpu_ns_median": st.median(r["query_cpu_ns"] for r in repeats),
               "heap_wall_ns_median": st.median(r["heap_wall_ns"] for r in repeats),
               "heap_cpu_ns_median": st.median(r["heap_cpu_ns"] for r in repeats),
               "paired_wall_saving_ns_median": st.median(r["paired_wall_saving_ns"] for r in repeats),
               "paired_cpu_saving_ns_median": st.median(r["paired_cpu_saving_ns"] for r in repeats),
               "wall_cpu_ratio_median": st.median(r["wall_cpu_ratio"] for r in repeats),
               "wall_cpu_ratio_max": max(r["wall_cpu_ratio"] for r in repeats),
               "forced_cleanup_wall_ns_median": st.median(r["forced_cleanup_wall_ns"] for r in repeats),
               "cold_training_plus_build_wall_ns_median": st.median(r["cold_training_plus_build_wall_ns"] for r in repeats),
               "cold_training_plus_build_cpu_ns_median": st.median(r["cold_training_plus_build_cpu_ns"] for r in repeats),
               "paired_repeats": repeats}
        for clock in ("wall", "cpu"):
            saving = row[f"paired_{clock}_saving_ns_median"]
            cost = row[f"cold_training_plus_build_{clock}_ns_median"]
            row[f"cold_break_even_queries_{clock}"] = cost/saving if saving > 0 else None
        source_rows.append(row)
    by_workload = defaultdict(dict)
    for workload, method in sorted({(r["workload"], r["method"]) for r in source_rows}):
        selected = [r for r in source_rows if r["workload"] == workload and r["method"] == method]
        by_workload[workload][method] = {
            "heldout_source_units": len(selected),
            "training_sources": len({r["train_source"] for r in selected}),
            "nested_timing_repeats": sum(r["repeat_count"] for r in selected),
            "source_median_wall_ratio": st.median(r["wall_ratio_median"] for r in selected),
            "source_median_cpu_ratio": st.median(r["cpu_ratio_median"] for r in selected),
            "source_median_query_wall_ns": st.median(r["query_wall_ns_median"] for r in selected),
            "source_median_query_cpu_ns": st.median(r["query_cpu_ns_median"] for r in selected),
            "observed_source_wall_ratio_range": [min(r["wall_ratio_median"] for r in selected), max(r["wall_ratio_median"] for r in selected)],
            "observed_source_cpu_ratio_range": [min(r["cpu_ratio_median"] for r in selected), max(r["cpu_ratio_median"] for r in selected)],
            "wall_ratio_cluster_interval": cluster_interval(selected, "wall_ratio_median", bootstrap_draws),
            "cpu_ratio_cluster_interval": cluster_interval(selected, "cpu_ratio_median", bootstrap_draws),
            "maximum_wall_cpu_ratio": max(r["wall_cpu_ratio_max"] for r in selected),
            "median_forced_cleanup_wall_ns": st.median(r["forced_cleanup_wall_ns_median"] for r in selected),
            "sources_without_positive_wall_saving": sum(r["cold_break_even_queries_wall"] is None for r in selected),
            "query_group_summaries": {group: {
                "source_units":sum(r["query_group"] == group for r in selected),
                "source_median_wall_ratio":st.median(r["wall_ratio_median"] for r in selected if r["query_group"] == group),
                "source_median_cpu_ratio":st.median(r["cpu_ratio_median"] for r in selected if r["query_group"] == group)}
                for group in sorted({r["query_group"] for r in selected})},
        }
    ablation_source_rows = []
    for (workload, train, query, group, numerator, denominator), repeats in sorted(ablation_units.items()):
        ablation_source_rows.append({"workload": workload, "train_source": train, "query_source": query,
            "query_group": group, "numerator": numerator, "denominator": denominator,
            "repeat_count": len(repeats), "paired_repeats": repeats,
            "wall_ratio_median": st.median(r["wall_ratio"] for r in repeats),
            "cpu_ratio_median": st.median(r["cpu_ratio"] for r in repeats)})
    ablations = defaultdict(dict)
    for workload, numerator, denominator in sorted({(r["workload"],r["numerator"],r["denominator"]) for r in ablation_source_rows}):
        selected = [r for r in ablation_source_rows if (r["workload"],r["numerator"],r["denominator"]) == (workload,numerator,denominator)]
        ablations[workload][numerator+"/"+denominator] = {
            "numerator": numerator, "denominator": denominator,
            "heldout_source_units": len(selected), "training_sources": len({r["train_source"] for r in selected}),
            "median_within_block_wall_ratio_collapsed_by_source": st.median(r["wall_ratio_median"] for r in selected),
            "median_within_block_cpu_ratio_collapsed_by_source": st.median(r["cpu_ratio_median"] for r in selected),
            "wall_ratio_cluster_interval": cluster_interval(selected,"wall_ratio_median",bootstrap_draws),
            "cpu_ratio_cluster_interval": cluster_interval(selected,"cpu_ratio_median",bootstrap_draws),
            "observed_source_wall_ratio_range": [min(r["wall_ratio_median"] for r in selected),max(r["wall_ratio_median"] for r in selected)],
            "observed_source_cpu_ratio_range": [min(r["cpu_ratio_median"] for r in selected),max(r["cpu_ratio_median"] for r in selected)],
        }
    successful_rows = [row for row in all_rows if row["status"] == "ok"]
    scheduling_diagnostics = {"successful_method_records":len(successful_rows),
        "wall_over_cpu_above_1_10":sum(r["wall_cpu_ratio"]>1.10 for r in successful_rows),
        "wall_over_cpu_above_1_25":sum(r["wall_cpu_ratio"]>1.25 for r in successful_rows),
        "cpu_over_wall_above_1_10":sum(1/r["wall_cpu_ratio"]>1.10 for r in successful_rows),
        "cpu_over_wall_above_1_25":sum(1/r["wall_cpu_ratio"]>1.25 for r in successful_rows),
        "records_discarded_by_threshold":0,
        "interpretation":"Descriptive timing disagreement; includes scheduling, clock overhead, and possible multithreading. No causal attribution or exclusion rule."}
    hash_signatures = Counter()
    hash_failures, hash_missing = [], []
    for path, document in included_documents:
        launch = document.get("source_sha256_at_launch")
        completion = document.get("source_sha256_at_completion")
        if launch is None or completion is None:
            hash_missing.append(str(path))
        else:
            hash_signatures[hashlib.sha256(json.dumps(launch, sort_keys=True).encode()).hexdigest()] += 1
            if launch != completion or document.get("code_unchanged_during_worker") is not True:
                hash_failures.append(str(path))
    timing_run_list = [{"file": str(path), "kind": document.get("kind"),
                       "status": document.get("status", "recorded"),
                       "spec": document.get("spec"),
                       "method_status_counts": dict(Counter(r["status"] for r in document.get("records", []))),
                       "launcher_elapsed_seconds": document.get("launcher_elapsed_seconds"),
                       "plan_sha256": document.get("plan_sha256"),
                       "code_unchanged_during_worker": document.get("code_unchanged_during_worker")}
                      for path, document in included_documents if document.get("kind") != "memory"]
    return {"kind": "phase2_summary", "input_files": [str(p) for p in paths],
            "paired_timing_blocks": len(block_ids), "timed_method_records": len(all_rows),
            "record_status_counts": dict(Counter(r["status"] for r in all_rows)),
            "failures": failures, "workloads": dict(by_workload), "source_units": source_rows,
            "within_block_ablations":dict(ablations), "ablation_source_units":ablation_source_rows,
            "scheduling_diagnostics":scheduling_diagnostics,
            "timing_run_list":timing_run_list,
            "source_hash_audit":{"included_worker_records":len(included_documents),
                                 "source_hash_set_counts":dict(hash_signatures),
                                 "changed_during_worker_files":hash_failures,
                                 "missing_hash_files":hash_missing},
            "memory_runs": memory, "training_records": training_records,
            "interpretation": [
                "Ratios >1 mean slower than native heapq for this Python kernel and workload.",
                "Repeated timings and queue seeds are nested within source units, not independent graphs.",
                "No cross-workload pooled significance test is performed.",
                "All raw failures are retained; successful-pair summaries must be read together with failure counts.",
                "Strict cold break-even requires q>T/(H-A); null means no positive observed saving, not proof of universal impossibility.",
                "Break-even is a hypothetical repeated-query estimate for each held-out source: median cold training/build cost divided by median paired within-block saving; no finite-sample uncertainty is assigned to the threshold.",
                "Cold training is charged in full; useful-query incremental accounting is not estimated.",
                "GC is disabled during query timing; forced cleanup is separately reported and not assumed equal to deployment garbage-collection cost.",
                "Wall/CPU ratios reveal possible scheduling noise but cannot prove host isolation.",
                "RSS values are process high-water marks; traced Python memory excludes untraced graph allocations and native allocations."]}


def completeness(plans, paths):
    """Audit frozen job coverage independently of successful-result selection."""
    observed = {}
    for path in paths:
        if path.name in observed:
            raise ValueError("Duplicate worker filename across inputs: " + path.name)
        observed[path.name] = json.loads(path.read_text())
    expected = {}
    mismatches = []
    for plan_path in plans:
        plan = json.loads(plan_path.read_text())
        plan_hash = hashlib.sha256(plan_path.read_bytes()).hexdigest()
        for kind, jobs, id_field in (("timing", plan["blocks"], "block_id"),
                                     ("memory", plan["memory"], "memory_id")):
            for spec in jobs:
                name = f"{kind}-{spec[id_field]}.json"
                if name in expected:
                    raise ValueError("Duplicate planned job: " + name)
                expected[name] = spec
                result = observed.get(name)
                if result is not None:
                    reasons = []
                    if result.get("spec") != spec:
                        reasons.append("spec differs from frozen plan")
                    if result.get("plan_sha256") != plan_hash:
                        reasons.append("plan hash differs")
                    if result.get("source_sha256_at_launch") != plan["planned_source_sha256"]:
                        reasons.append("launch source hash differs")
                    if kind == "timing" and result.get("kind") == "timing_block":
                        if Counter(r["method"] for r in result["records"]) != Counter(spec["methods"]):
                            reasons.append("method coverage differs")
                    if reasons:
                        mismatches.append({"file":name,"reasons":reasons})
    return {"plan_files":[str(p) for p in plans], "planned_jobs":len(expected),
            "observed_jobs":len(observed), "missing_jobs":sorted(set(expected)-set(observed)),
            "unexpected_jobs":sorted(set(observed)-set(expected)), "mismatches":mismatches,
            "complete_and_matching":not (set(expected)^set(observed) or mismatches)}


def write_tables(summary, directory):
    directory.mkdir(parents=True, exist_ok=True)
    timing = []
    for workload, methods in summary["workloads"].items():
        for method, values in methods.items():
            row = {"workload":workload, "method":method}
            for field in ("heldout_source_units", "training_sources", "nested_timing_repeats",
                          "source_median_wall_ratio", "source_median_cpu_ratio",
                          "source_median_query_wall_ns", "source_median_query_cpu_ns",
                          "maximum_wall_cpu_ratio", "median_forced_cleanup_wall_ns",
                          "sources_without_positive_wall_saving"):
                row[field] = values[field]
            for clock in ("wall", "cpu"):
                interval = values[clock+"_ratio_cluster_interval"] or {}
                row[clock+"_ratio_interval_lower"] = interval.get("lower")
                row[clock+"_ratio_interval_upper"] = interval.get("upper")
            timing.append(row)
    ablations = []
    for workload, comparisons in summary["within_block_ablations"].items():
        for comparison, values in comparisons.items():
            row = {"workload":workload, "comparison":comparison}
            for clock in ("wall", "cpu"):
                row[clock+"_ratio"] = values["median_within_block_"+clock+"_ratio_collapsed_by_source"]
                interval = values[clock+"_ratio_cluster_interval"] or {}
                row[clock+"_ratio_interval_lower"] = interval.get("lower")
                row[clock+"_ratio_interval_upper"] = interval.get("upper")
            ablations.append(row)
    memory = []
    for record in summary["memory_runs"]:
        row = {"workload":record["spec"]["workload"], "method":record["spec"]["method"],
               "status":record["status"], "train_source":record["spec"]["train_source"],
               "query_source":record["spec"]["query_source"]}
        row.update({k:v for k,v in record.items() if k.startswith(("python_traced_", "rss_"))})
        row["post_query_queue_stats_json"] = json.dumps(record.get("post_query_queue_stats"), sort_keys=True)
        memory.append(row)
    names = []
    for name, rows in (("timing_summary.csv",timing), ("ablation_summary.csv",ablations), ("memory_summary.csv",memory)):
        if not rows:
            continue
        path = directory/name
        with path.open("w", newline="") as handle:
            fields = list(dict.fromkeys(k for row in rows for k in row))
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        names.append(str(path))
    return names


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directories", nargs="+")
    parser.add_argument("--output", required=True)
    parser.add_argument("--bootstrap-draws", type=int, default=2000)
    parser.add_argument("--include-pilot", action="store_true")
    parser.add_argument("--plans", nargs="*", default=[])
    parser.add_argument("--table-directory")
    args = parser.parse_args()
    paths = []
    for directory in args.directories:
        paths.extend(sorted(Path(directory).glob("timing-*.json")))
        paths.extend(sorted(Path(directory).glob("memory-*.json")))
    output = analyze(paths, args.bootstrap_draws, args.include_pilot)
    if args.plans:
        output["frozen_plan_completeness"] = completeness([Path(p) for p in args.plans], paths)
    if args.table_directory:
        output["compact_tables"] = write_tables(output, Path(args.table_directory))
    Path(args.output).write_text(json.dumps(output, indent=2)+"\n")
    print(json.dumps({"summary": args.output, "blocks": output["paired_timing_blocks"],
                      "timed_records": output["timed_method_records"], "failures": len(output["failures"])}))


if __name__ == "__main__":
    main()
