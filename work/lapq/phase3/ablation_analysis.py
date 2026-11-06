#!/usr/bin/env python3
"""Analyze the frozen occurrence-preserving ablation without running benchmarks.

Every ratio, including the factorial interaction, is formed within a paired
block before repetitions are collapsed. Three fitted-source clusters support
exploratory conditional intervals, not population-level graph inference.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import random
import statistics as st

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CLOCKS = ("wall", "cpu")
METHODS = ("occ_list_retain", "occ_list_map", "occ_linked_retain",
           "occ_linked_map", "grouped_compact_reference")
PAIRS = {
    "map_cleanup_list": ("occ_list_map", "occ_list_retain"),
    "map_cleanup_linked": ("occ_linked_map", "occ_linked_retain"),
    "bucket_replace_retained": ("occ_linked_retain", "occ_list_retain"),
    "bucket_replace_map": ("occ_linked_map", "occ_list_map"),
    "grouped_bridge": ("grouped_compact_reference", "occ_linked_map"),
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def quantile(values, probability):
    values = sorted(values)
    position = (len(values)-1)*probability
    lower = int(position)
    return values[lower] + (position-lower)*(values[min(lower+1,len(values)-1)]-values[lower])


def interval(units, field, draws=2000, seed=918342):
    clusters = defaultdict(list)
    for unit in units:
        clusters[unit["train_source"]].append(unit)
    if len(clusters) < 2 or draws < 1:
        return None
    rng, estimates = random.Random(seed), []
    names = sorted(clusters)
    for _ in range(draws):
        values = []
        for name in rng.choices(names, k=len(names)):
            strata = defaultdict(list)
            for unit in clusters[name]:
                strata[unit["query_group"]].append(unit)
            for group in sorted(strata):
                sample = strata[group]
                values.extend(r[field] for r in rng.choices(sample,k=len(sample)))
        estimates.append(st.median(values))
    return {"lower":quantile(estimates,.025), "upper":quantile(estimates,.975),
            "resamples":draws, "training_clusters":len(names),
            "method":"Exploratory percentile bootstrap of fitted-training-source clusters, with query sources resampled within near/far strata; repetitions already collapsed.",
            "scope":"Conditional on these reused graph instances and selected source/model cases. Only three clusters per target; no graph-population coverage or confirmatory significance claim."}


def positive_duration(row, clock):
    value = row.get(f"query_{clock}_ns")
    return isinstance(value,(int,float)) and math.isfinite(value) and value > 0


def timing_analysis(documents, protocol, draws=2000):
    """documents is an iterable of (path, normalized phase-two-style record)."""
    pair_units, method_units = defaultdict(list), defaultdict(list)
    rows, failures, block_list, observed_ids = [], [], [], set()
    pair_coverage = Counter()
    for path, document in documents:
        spec = document["spec"]
        block_id = spec["block_id"]
        if block_id in observed_ids:
            raise ValueError("Duplicate timing block: "+block_id)
        observed_ids.add(block_id)
        records = document.get("records", [])
        block_list.append({"file":str(path),"block_id":block_id,"spec":spec,
                           "status":document.get("status","recorded"),
                           "record_status_counts":dict(Counter(r.get("status") for r in records))})
        if document.get("status") != "ok":
            failures.append({"file":str(path),"block_id":block_id,
                             "reason":"Non-ok top-level block status: "+str(document.get("status"))})
        if not records:
            failures.append({"file":str(path),"block_id":block_id,
                             "reason":document.get("status","missing method records")})
            continue
        counts = Counter(r["method"] for r in records)
        if counts != Counter(METHODS):
            failures.append({"file":str(path),"block_id":block_id,
                             "reason":"Method coverage differs", "observed":dict(counts)})
        good = {}
        work_signatures, occurrence_heights, distance_digests = [], [], []
        for record in records:
            row = {"file":str(path),"spec":spec,**record}
            rows.append(row)
            if record.get("status") != "ok":
                failures.append({"file":str(path),"block_id":block_id,
                                 "method":record["method"],"reason":record.get("error",record.get("status"))})
                continue
            if not all(positive_duration(record,c) for c in CLOCKS):
                failures.append({"file":str(path),"block_id":block_id,
                                 "method":record["method"],"reason":"Nonpositive/nonfinite clock measurement"})
                continue
            validation = record.get("untimed_validation",{})
            inserts, extracts = validation.get("insertions"), validation.get("extractions")
            digest = record.get("distance_digest")
            if (validation.get("fully_drained") is not True
                    or not isinstance(inserts,int) or inserts < 1 or inserts != extracts
                    or not isinstance(digest,str) or len(digest)!=64
                    or any(c not in "0123456789abcdef" for c in digest)):
                failures.append({"file":str(path),"block_id":block_id,"method":record["method"],
                                 "reason":"Missing/invalid recorded warmup drain/count or distance-digest evidence"})
                continue
            work_signatures.append((inserts,extracts))
            distance_digests.append(digest)
            if record["method"] in METHODS[:4]:
                height = validation.get("stats",{}).get("max_skiplist_height")
                if not isinstance(height,int) or height < 1:
                    failures.append({"file":str(path),"block_id":block_id,"method":record["method"],
                                     "reason":"Missing occurrence maximum-height evidence"})
                occurrence_heights.append(height)
            if counts[record["method"]] != 1:
                continue
            good[record["method"]] = record
            identity = (spec["workload"],spec["train_source"],spec["query_source"],spec["query_group"],record["method"])
            method_units[identity].append({"repeat":spec["repeat"],"queue_seed":spec["queue_seed"],
                **{f"query_{c}_ns":record[f"query_{c}_ns"] for c in CLOCKS},
                **{f"forced_cleanup_{c}_ns":record.get(f"forced_cleanup_{c}_ns") for c in CLOCKS}})
        if (len(set(work_signatures))>1 or len(set(distance_digests))>1
                or len(set(occurrence_heights))>1):
            failures.append({"file":str(path),"block_id":block_id,
                             "reason":"Recorded method operation counts, distance digests, or occurrence heights differ"})
        for contrast in protocol["contrasts"]:
            name = contrast["id"]
            required = METHODS[:4] if name == "interaction" else PAIRS[name]
            if not all(m in good for m in required):
                continue
            pair = {"repeat":spec["repeat"],"queue_seed":spec["queue_seed"],
                    "block_id":block_id}
            for clock in CLOCKS:
                t = {m:good[m][f"query_{clock}_ns"] for m in required}
                if name == "interaction":
                    pair[f"{clock}_ratio"] = ((t["occ_linked_map"]/t["occ_list_map"])
                                                 /(t["occ_linked_retain"]/t["occ_list_retain"]))
                    pair[f"{clock}_arm_durations_ns"] = t
                else:
                    numerator, denominator = PAIRS[name]
                    a, b = t[numerator], t[denominator]
                    pair[f"{clock}_ratio"] = a/b
                    pair[f"{clock}_numerator_ns"] = a
                    pair[f"{clock}_denominator_ns"] = b
                    pair[f"{clock}_paired_difference_ns"] = a-b
            identity = (spec["workload"],spec["train_source"],spec["query_source"],spec["query_group"],name)
            pair_units[identity].append(pair)
            pair_coverage[(spec["workload"],name)] += 1
    source_units = []
    for (workload,train,query,group,name), repeated in sorted(pair_units.items()):
        unit = {"workload":workload,"train_source":train,"query_source":query,
                "query_group":group,"contrast":name,"repeat_count":len(repeated),
                "paired_repeats":repeated}
        for clock in CLOCKS:
            unit[f"{clock}_ratio_median"] = st.median(r[f"{clock}_ratio"] for r in repeated)
            if name != "interaction":
                unit[f"{clock}_paired_difference_ns_median"] = st.median(r[f"{clock}_paired_difference_ns"] for r in repeated)
        source_units.append(unit)
    by_workload = defaultdict(dict)
    for workload,name in sorted({(r["workload"],r["contrast"]) for r in source_units}):
        selected = [r for r in source_units if (r["workload"],r["contrast"]) == (workload,name)]
        summary = {"source_units":len(selected), "training_clusters":len({r["train_source"] for r in selected}),
                   "paired_blocks":pair_coverage[(workload,name)],
                   "source_repeat_counts":[r["repeat_count"] for r in selected]}
        for clock in CLOCKS:
            field = f"{clock}_ratio_median"
            summary[f"{clock}_ratio"] = st.median(r[field] for r in selected)
            summary[f"{clock}_observed_source_range"] = [min(r[field] for r in selected),max(r[field] for r in selected)]
            summary[f"{clock}_exploratory_interval"] = interval(selected,field,draws)
        summary["query_groups"] = {group:{"source_units":sum(r["query_group"]==group for r in selected),
            **{f"{clock}_ratio":st.median(r[f"{clock}_ratio_median"] for r in selected if r["query_group"]==group)
               for clock in CLOCKS}} for group in sorted({r["query_group"] for r in selected})}
        by_workload[workload][name] = summary
    absolute_units = []
    for (workload,train,query,group,method), repeated in sorted(method_units.items()):
        unit = {"workload":workload,"train_source":train,"query_source":query,
                "query_group":group,"method":method,"repeat_count":len(repeated),"repeats":repeated}
        for clock in CLOCKS:
            unit[f"query_{clock}_ns_median"] = st.median(r[f"query_{clock}_ns"] for r in repeated)
            cleanup = [r[f"forced_cleanup_{clock}_ns"] for r in repeated if r[f"forced_cleanup_{clock}_ns"] is not None]
            unit[f"forced_cleanup_{clock}_ns_median"] = st.median(cleanup) if cleanup else None
        absolute_units.append(unit)
    valid = [r for r in rows if r.get("status")=="ok" and all(positive_duration(r,c) for c in CLOCKS)]
    ratios = [r["query_wall_ns"]/r["query_cpu_ns"] for r in valid]
    diagnostics = {"valid_clock_records":len(valid),"wall_over_cpu_above_1_10":sum(r>1.10 for r in ratios),
                   "wall_over_cpu_above_1_25":sum(r>1.25 for r in ratios),
                   "cpu_over_wall_above_1_10":sum(1/r>1.10 for r in ratios),
                   "cpu_over_wall_above_1_25":sum(1/r>1.25 for r in ratios),
                   "maximum_wall_cpu_ratio":max(ratios,default=None),"records_discarded_by_scheduling_threshold":0,
                   "interpretation":"Descriptive clock disagreement, not a causal attribution of contention or an exclusion rule."}
    return {"timing_blocks":len(observed_ids),"method_records":len(rows),
            "record_status_counts":dict(Counter(r.get("status") for r in rows)),
            "failures":failures,"workloads":dict(by_workload),"source_units":source_units,
            "absolute_method_source_units":absolute_units,"scheduling_diagnostics":diagnostics,
            "timing_run_list":block_list,"contrasts":protocol["contrasts"]}


def write_csv(path, rows):
    rows = list(rows)
    if not rows:
        return
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with Path(path).open("w",newline="") as stream:
        writer = csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k:json.dumps(v,sort_keys=True) if isinstance(v,(dict,list)) else v for k,v in row.items()})


def write_timing_tables(summary, directory):
    directory = Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    rows = []
    for workload, contrasts in summary["workloads"].items():
        for name, values in contrasts.items():
            row = {"workload":workload,"contrast":name,"source_units":values["source_units"],
                   "training_clusters":values["training_clusters"],"paired_blocks":values["paired_blocks"]}
            for clock in CLOCKS:
                row[f"{clock}_ratio"] = values[f"{clock}_ratio"]
                ci = values[f"{clock}_exploratory_interval"] or {}
                row[f"{clock}_interval_lower"] = ci.get("lower")
                row[f"{clock}_interval_upper"] = ci.get("upper")
                row[f"{clock}_observed_source_range"] = values[f"{clock}_observed_source_range"]
            rows.append(row)
    write_csv(directory/"timing_contrasts.csv",rows)
    write_csv(directory/"timing_source_units.csv",({k:v for k,v in row.items() if k!="paired_repeats"} for row in summary["source_units"]))
    write_csv(directory/"timing_absolute.csv",({k:v for k,v in row.items() if k!="repeats"} for row in summary["absolute_method_source_units"]))


def memory_analysis(documents):
    """Keep every phase/mode explicit; fixed-source memory has no bootstrap CI."""
    runs, table_rows, failures = [], [], []
    for path, document in documents:
        record = {"file":str(path),**document}
        spec = document["spec"]
        if document.get("status") != "ok":
            failures.append({"file":str(path),"spec":spec,
                             "reason":document.get("error",document.get("status"))})
        else:
            required = ["python_traced_empty_queue_alive_before_gc_bytes",
                        "python_traced_empty_queue_alive_after_gc_bytes",
                        "python_traced_after_queue_destroyed_bytes"]
            if document["kind"] == "graph_memory":
                required += ["python_traced_training_and_build_peak_bytes",
                             "python_traced_model_retained_bytes",
                             "python_traced_query_peak_including_model_bytes",
                             "python_traced_post_query_retained_bytes",
                             "python_traced_after_advisor_destroyed_bytes"]
                if document.get("distance_vector_verified") is not True or document.get("fully_drained") is not True:
                    failures.append({"file":str(path),"reason":"Missing graph-memory correctness/drain validation"})
            else:
                required += ["python_traced_current_at_live_limit_bytes",
                             "python_traced_peak_at_live_limit_bytes","python_traced_total_peak_bytes"]
                if document.get("all_extractions_correct") is not True or document.get("extractions") != spec["insertions"]:
                    failures.append({"file":str(path),"reason":"Missing retention correctness/extraction validation"})
            for field in required:
                value = document.get(field)
                if not isinstance(value,(int,float)) or not math.isfinite(value) or value < 0:
                    failures.append({"file":str(path),"reason":"Missing/invalid memory field: "+field})
        training = document.get("python_traced_training_and_build_peak_bytes")
        query = document.get("python_traced_query_peak_including_model_bytes")
        if isinstance(training,(int,float)) and isinstance(query,(int,float)):
            record["derived_fitted_run_peak_bytes"] = max(training,query)
        before = document.get("python_traced_empty_queue_alive_before_gc_bytes")
        after = document.get("python_traced_empty_queue_alive_after_gc_bytes")
        if isinstance(before,(int,float)) and isinstance(after,(int,float)):
            record["derived_before_minus_after_gc_bytes"] = before-after
        runs.append(record)
        row = {"file":str(path),"kind":document.get("kind"),"status":document.get("status"),**spec}
        row.update({k:v for k,v in record.items()
                    if k.startswith(("python_traced_","rss_","traced_","derived_"))})
        # Introspection is preserved separately from the actual tracing snapshot.
        row.update({k:v for k,v in record.items() if k.endswith("stats")})
        for field in ("post_snapshot_stats_buffer_traced_increment_bytes",
                      "explicit_collection_objects_while_queue_alive"):
            row[field] = record.get(field)
        table_rows.append(row)
    grouped = defaultdict(dict)
    for record in runs:
        if record.get("status")!="ok":
            continue
        spec = record["spec"]
        identity = (record["kind"],spec.get("case_id"),spec.get("insertions"),spec["method"])
        grouped[identity][spec["gc_mode"]] = record
    mode_pairs, peak_differences = [], defaultdict(list)
    for (kind,case_id,insertions,method), modes in sorted(grouped.items(),key=lambda item:repr(item[0])):
        disabled = "disabled_during_query" if kind=="graph_memory" else "disabled_during_trace"
        if "enabled" not in modes or disabled not in modes:
            continue
        a,b = modes["enabled"],modes[disabled]
        fields = ["python_traced_empty_queue_alive_before_gc_bytes",
                  "python_traced_empty_queue_alive_after_gc_bytes",
                  "python_traced_after_queue_destroyed_bytes"]
        peak_field = ("python_traced_query_peak_including_model_bytes" if kind=="graph_memory"
                      else "python_traced_total_peak_bytes")
        fields.append(peak_field)
        comparisons = {f:{"enabled_bytes":a[f],"disabled_bytes":b[f],
                          "enabled_minus_disabled_bytes":a[f]-b[f]} for f in fields
                       if f in a and f in b}
        if peak_field in a and peak_field in b:
            peak_differences[kind].append(abs(a[peak_field]-b[peak_field]))
        mode_pairs.append({"kind":kind,"case_id":case_id,"insertions":insertions,"method":method,
                           "enabled_file":a["file"],"disabled_file":b["file"],"phases":comparisons})
    mode_diagnostics = {"matched_gc_mode_pairs":len(mode_pairs),
        "pairs_by_kind":dict(Counter(r["kind"] for r in mode_pairs)),
        "maximum_absolute_query_or_trace_peak_mode_difference_bytes":{k:max(v) for k,v in peak_differences.items()},
        "explicit_collection_objects_while_queue_alive_counts":dict(Counter(
            r.get("explicit_collection_objects_while_queue_alive") for r in runs if r.get("status")=="ok")),
        "scope":"Descriptive paired cases from separate memory processes; not timing evidence or a confidence interval. A before/after full-GC allocation decrease need not mean cycle reclamation, because collection can also change interpreter free lists."}
    return {"runs":runs,"table_rows":table_rows,"failures":failures,
            "gc_mode_pairs":mode_pairs,"gc_mode_diagnostics":mode_diagnostics,
            "interpretation":[
                "Each graph/model/method/GC-mode memory case is measured in a separate process; there is only one fixed held-out source per target, so no source-level interval is inferred.",
                "Training/build and query-phase peaks have different boundaries. Fitted-run peak is their maximum, not their sum; query peak alone is not cold fitted-run peak.",
                "The retained advisor remains part of traced graph-query measurements unless a field explicitly records its destruction. Baseline subtraction is not asserted to isolate queue storage exactly.",
                "Full post-query allocations include distance/routing outputs. Empty-queue retention before and after collection must be read with the runner's explicit object-lifetime boundaries.",
                "A map entry can disappear while a node remains reachable through other references or unreachable cycles await collection. Compare before/after GC and enabled/disabled modes rather than interpreting map cardinality as total object memory.",
                "RSS fields are process lifetime high-water values; their differences are not component sizes. Graph/oracle and native allocations are not fully measured by tracemalloc.",
                "Bounded-live traces use unique priorities and constant advice; grouping is inactive, but other compact-policy differences remain. These memory-only runs are not timing evidence.",
                "Compared with phase two, these workers fully prewarm RNG and omit a large training-validation digest-string allocation. Absolute residual/training peaks across phases must not be attributed solely to adapter changes; the within-phase-three arms share these refined boundaries."]}


def audit_records(protocol_path, manifest_path, result_directory):
    """Manifest adapter follows the explicit job/spec/output-name runner schema."""
    protocol_path, manifest_path = Path(protocol_path), Path(manifest_path)
    protocol = json.loads(protocol_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    directory = Path(result_directory)
    protocol_hash, manifest_hash = sha256(protocol_path), sha256(manifest_path)
    issues, missing, documents, expected_names = [], [], [], set()
    if manifest.get("protocol_sha256") != protocol_hash:
        issues.append({"scope":"manifest","reason":"Frozen protocol hash mismatch"})
    if protocol.get("preflight",{}).get("status") != "passed":
        issues.append({"scope":"protocol","reason":"No passing frozen semantic preflight"})
    source_hashes = manifest.get("source_sha256")
    if not source_hashes:
        issues.append({"scope":"manifest","reason":"Missing executed source hashes"})
    else:
        for name, expected in source_hashes.items():
            path = ROOT/name
            if not path.is_file() or sha256(path) != expected:
                issues.append({"scope":"current_artifact","file":name,"reason":"Current source/evidence file differs from execution manifest"})
        for name, expected in protocol["source_sha256"].items():
            if source_hashes.get(name) != expected:
                issues.append({"scope":"manifest","file":name,"reason":"Executed-source pin differs from frozen proposal"})
    jobs = manifest["jobs"]
    ids = [j["job_id"] for j in jobs]
    if len(ids) != len(set(ids)):
        issues.append({"scope":"manifest","reason":"Duplicate job identifiers"})
    frozen_blocks = {b["block_id"]:b for b in protocol["blocks"]}
    frozen_cases = {c["case_id"]:c for c in protocol["source_cases"]}
    gate_path = HERE/"full_trace_gate_results.json"
    gate_cases = []
    if not gate_path.is_file():
        issues.append({"scope":"trace_gate","reason":"Full-workload trace gate missing"})
    else:
        gate = json.loads(gate_path.read_text())
        expected_gate_hash = manifest.get("full_trace_gate",{}).get("result_sha256")
        if (sha256(gate_path)!=expected_gate_hash or gate.get("status")!="passed"
                or gate.get("protocol_sha256")!=protocol_hash):
            issues.append({"scope":"trace_gate","reason":"Full-workload trace gate status/hash/protocol mismatch"})
        for entry in gate.get("cases",[]):
            path = ROOT/entry["file"]
            if not path.is_file() or sha256(path)!=entry["sha256"]:
                issues.append({"scope":"trace_gate","file":entry["file"],"reason":"Trace-case file hash mismatch"})
                continue
            case_record = json.loads(path.read_text())
            case = case_record["case"]
            gate_cases.append(case["case_id"])
            seed = next((b["queue_seed"] for b in protocol["blocks"]
                         if b["case_id"]==case["case_id"] and b["repeat"]==0),None)
            records = case_record.get("records",[])
            if (frozen_cases.get(case["case_id"])!=case or case_record.get("status")!="passed"
                    or case_record.get("seed_repeat")!=0 or case_record.get("queue_seed")!=seed
                    or Counter(r.get("method") for r in records)!=Counter(METHODS[:4])):
                issues.append({"scope":"trace_gate","file":entry["file"],"reason":"Trace-case spec/status/method coverage mismatch"})
            if records:
                baseline = records[0]
                for record in records:
                    if (record.get("status")!="ok" or record.get("streams")!=baseline.get("streams")
                            or record.get("distance_digest")!=baseline.get("distance_digest")
                            or record.get("insertions")!=record.get("extractions")
                            or record.get("insertions")!=baseline.get("insertions")):
                        issues.append({"scope":"trace_gate","file":entry["file"],"reason":"Trace stream/distance/drain parity mismatch"})
        if Counter(gate_cases)!=Counter(frozen_cases.keys()):
            issues.append({"scope":"trace_gate","reason":"Trace-case coverage differs from frozen source cases"})
    seen_timing = set()
    graph_memory_specs, retention_specs = [], []
    for ordinal, job in enumerate(jobs):
        name = job["output_name"]
        if Path(name).name != name or name in expected_names:
            raise ValueError("Unsafe or duplicate output name: "+name)
        expected_names.add(name)
        if job["kind"] == "timing":
            identifier = job["spec"]["block_id"]
            seen_timing.add(identifier)
            if frozen_blocks.get(identifier) != job["spec"]:
                issues.append({"job_id":job["job_id"],"reason":"Timing spec differs from frozen protocol"})
        elif job["kind"] == "graph_memory":
            graph_memory_specs.append(job["spec"])
        elif job["kind"] == "retention":
            retention_specs.append(job["spec"])
        path = directory/name
        if not path.exists():
            missing.append(name)
            continue
        try:
            document = json.loads(path.read_text())
        except (ValueError,OSError) as error:
            issues.append({"file":name,"reason":"Unreadable worker record","error":str(error)})
            continue
        documents.append((path,document))
        expected_kind = "timing_block" if job["kind"]=="timing" else job["kind"]
        checks = {"spec":job["spec"],"kind":expected_kind,
                  "job_id":job["job_id"],"execution_ordinal":ordinal,
                  "protocol_sha256":protocol_hash,"execution_manifest_sha256":manifest_hash,
                  "source_sha256_at_launch":source_hashes,
                  "source_sha256_at_completion":source_hashes,
                  "code_unchanged_during_worker":True}
        for field, expected in checks.items():
            # Failed subprocess records can retain launcher kind 'timing'.
            if field == "kind" and document.get("status") in ("worker_error","timeout"):
                if document.get(field) not in (job["kind"],expected_kind):
                    issues.append({"file":name,"reason":"Unexpected failure-record kind"})
                continue
            if document.get(field) != expected:
                issues.append({"file":name,"reason":field+" mismatch"})
        if job["kind"] == "timing" and document.get("records"):
            order = document.get("method_order")
            if order != job["spec"]["method_order"]:
                issues.append({"file":name,"reason":"Executed method order differs from plan"})
            if Counter(r.get("method") for r in document["records"]) != Counter(protocol["methods"]):
                issues.append({"file":name,"reason":"Method record coverage differs"})
            if [r.get("method") for r in document["records"]] != job["spec"]["method_order"]:
                issues.append({"file":name,"reason":"Method record order differs"})
            if any(r.get("queue_seed") != job["spec"]["queue_seed"] for r in document["records"]):
                issues.append({"file":name,"reason":"Recorded method seed differs"})
            if document.get("status") == "ok" and document.get("occurrence_warmup_work_parity_passed") is not True:
                issues.append({"file":name,"reason":"No passing occurrence warmup parity gate"})
    if seen_timing != set(frozen_blocks):
        issues.append({"scope":"manifest","reason":"Timing job coverage differs from frozen protocol",
                       "missing":sorted(set(frozen_blocks)-seen_timing),"extra":sorted(seen_timing-set(frozen_blocks))})
    planned_counts = {"timing":protocol["timing"]["planned_blocks"],
                      "graph_memory":protocol["memory"]["planned_graph_workers"],
                      "retention":protocol["bounded_live_diagnostic"]["planned_workers"]}
    if Counter(j["kind"] for j in jobs) != Counter(planned_counts):
        issues.append({"scope":"manifest","reason":"Job-kind counts differ from frozen protocol"})
    memory_cases = {c["case_id"]:c for c in protocol["source_cases"]
                    if c["train_index"]==0 and c["query_group"]=="far"}
    expected_memory = Counter((c,m,g) for c in memory_cases for m in protocol["methods"]
                              for g in protocol["memory"]["gc_modes"])
    observed_memory = Counter((s["case_id"],s["method"],s["gc_mode"]) for s in graph_memory_specs)
    if observed_memory != expected_memory:
        issues.append({"scope":"manifest","reason":"Graph-memory case/method/GC-mode coverage differs"})
    for spec in graph_memory_specs:
        case = memory_cases.get(spec["case_id"],{})
        seed = next((b["queue_seed"] for b in protocol["blocks"]
                     if b["case_id"]==spec["case_id"] and b["repeat"]==0),None)
        if any(spec.get(k)!=v for k,v in case.items()) or spec.get("queue_seed")!=seed:
            issues.append({"scope":"manifest","reason":"Graph-memory case fields/seed differ from frozen source case","spec":spec})
    retention_design = protocol["bounded_live_diagnostic"]
    expected_retention = Counter((m,n,g) for m in retention_design["methods"]
                                 for n in retention_design["insertions"] for g in retention_design["gc_modes"])
    observed_retention = Counter((s["method"],s["insertions"],s["gc_mode"]) for s in retention_specs)
    if observed_retention != expected_retention:
        issues.append({"scope":"manifest","reason":"Retention method/length/GC-mode coverage differs"})
    if any(s.get("max_live")!=retention_design["max_live"] or s.get("prediction")!="constant0"
           or s.get("rank_universe")!=1 or s.get("queue_seed")!=101 for s in retention_specs):
        issues.append({"scope":"manifest","reason":"Retention occupancy/prediction/universe/seed differs"})
    observed_names = {p.name for p in directory.glob("*.json")
                      if p.name.startswith(("timing-","graph_memory-","retention-"))}
    unexpected = sorted(observed_names-expected_names)
    status_path = directory/"run_status.json"
    run_status = json.loads(status_path.read_text()) if status_path.exists() else {}
    if run_status.get("all_jobs_completed") is not True or run_status.get("execution_manifest_sha256") != manifest_hash:
        issues.append({"scope":"run_status","reason":"Missing matching completion marker"})
    return protocol, manifest, documents, {
        "protocol_file":str(protocol_path),"protocol_sha256":protocol_hash,
        "execution_manifest_file":str(manifest_path),"execution_manifest_sha256":manifest_hash,
        "planned_worker_records":len(jobs),"readable_worker_records":len(documents),
        "planned_kind_counts":dict(Counter(j["kind"] for j in jobs)),
        "missing_worker_files":missing,"unexpected_worker_files":unexpected,
        "mismatches":issues,"complete_and_matching":not (missing or unexpected or issues),
        "executed_source_sha256":source_hashes,"run_status":run_status,
        "full_trace_gate_cases_audited":len(gate_cases),
        "full_trace_gate_scope":"All 24 source cases, four occurrence variants, repeat-zero seed only; not every timed seed or a universal equivalence proof."}


def analyze(protocol_path, manifest_path, result_directory, draws=2000):
    protocol, manifest, documents, audit = audit_records(protocol_path,manifest_path,result_directory)
    timing = [(p,d) for p,d in documents if d.get("kind") in ("timing","timing_block")]
    memory = [(p,d) for p,d in documents if d.get("kind") in ("graph_memory","retention")]
    output = timing_analysis(timing,protocol,draws)
    memory_output = memory_analysis(memory)
    output.update(kind="phase3_ablation_summary",schema_version=1,
                  analysis_source_sha256=sha256(__file__),frozen_protocol=protocol,
                  execution_manifest=manifest,coverage=audit,
                  memory_runs=memory_output["runs"],memory_interpretation=memory_output["interpretation"],
                  gc_mode_pairs=memory_output["gc_mode_pairs"],gc_mode_diagnostics=memory_output["gc_mode_diagnostics"])
    output["failures"].extend(memory_output["failures"])
    output["attribution_gate_passed"] = audit["complete_and_matching"] and not output["failures"]
    output["interpretation"] = [
        "All timing ratios, including interaction, are calculated within a paired block, then collapsed by held-out query/training source before target-level summarization.",
        "Ratios below one favor the numerator method. Interaction is (linked_map/list_map)/(linked_retain/list_retain), not a ratio of separately aggregated effects.",
        "The two-by-two contrasts identify conditional implementation effects in the frozen occurrence-preserving adapters. They do not erase added hashing, allocation, or container costs.",
        "The grouped bridge is a coupled policy comparison, not an isolated causal effect of duplicate grouping.",
        "Failures and coverage/hash discrepancies must be read with successful-pair summaries. Missing/failing pairs are never silently substituted or rerun by this analysis.",
        "The attribution gate combines frozen semantic preflight, worker correctness and complete hash/spec coverage; it is not a proof of semantic equivalence on every possible input.",
        "Graph instances were selected after phase-two findings and reused. This is a post-hoc mechanism study, not an independent confirmatory graph sample.",
        "Three training clusters, one shared host, disabled query GC and few source cases limit interpretation. Scheduling thresholds are descriptive only."]
    output["interpretation"].append("Forced cleanup includes dropping the complete result (distances, routing and queue) followed by full gc.collect; it is not isolated node-reclamation time or a deployment garbage-collection schedule.")
    return output,memory_output["table_rows"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol",default=str(HERE/"ablation_protocol.json"))
    parser.add_argument("--manifest",required=True)
    parser.add_argument("--results",required=True)
    parser.add_argument("--output",default=str(HERE/"ablation_summary.json"))
    parser.add_argument("--tables",default=str(HERE/"tables"))
    parser.add_argument("--bootstrap-draws",type=int,default=2000)
    args = parser.parse_args()
    output,memory_rows = analyze(args.protocol,args.manifest,args.results,args.bootstrap_draws)
    write_timing_tables(output,args.tables)
    write_csv(Path(args.tables)/"memory_phases.csv",memory_rows)
    write_csv(Path(args.tables)/"memory_gc_mode_pairs.csv",output["gc_mode_pairs"])
    output_path = Path(args.output)
    temporary = output_path.with_suffix(output_path.suffix+".tmp")
    temporary.write_text(json.dumps(output,indent=2)+"\n")
    temporary.replace(output_path)
    print(json.dumps({"summary":args.output,"timing_blocks":output["timing_blocks"],
                      "timed_records":output["method_records"],"memory_workers":len(output["memory_runs"]),
                      "failures":len(output["failures"]),"attribution_gate_passed":output["attribution_gate_passed"]}))
    if not output["attribution_gate_passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

