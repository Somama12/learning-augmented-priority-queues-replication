#!/usr/bin/env python3
"""Small differential checks only: no primary replication data are generated.

The independent C++ engine is compared with the unchanged released sortSL,
sortOSL and sortDC functions. Only the randomness providers and diagnostic hooks
are temporarily replaced to provide identical orders/heights and observe counts.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import io
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
UPSTREAM = HERE.parent / "upstream"
PIN = "3915cc261e87949b2b0aa724673c2227492d1b08"
PINS = {
    "skiplist.py": "63282bd3c84e89684380ab690c48f43a24fa013f4323a57e045fc94fc96b4a3c",
    "sorting.py": "d477f8c63161371f0480b152c5ec501f9ea09628ca7579fbdde1d40246ff9de9",
    "predictions.py": "19694c93a8695d4a889a9dba92cc23fc6c31efb41a13b45693aa914d9ccab30d",
    "heaps.py": "87c1b4d215254ffbc3c084b246a576e859febb7bcc99d4a780d7db17d052e605",
}
MASK = (1 << 64) - 1


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def cases():
    rng = random.Random(20261106)
    result = []
    for n in [1, 2, 3, 7, 16, 31, 64]:
        patterns = {
            "perfect": list(range(n)),
            "reverse": list(reversed(range(n))),
            "constant": [0] * n,
            "negative_ties": [-3 + i // 3 for i in range(n)],
            "signed_random": [rng.randrange(-n - 3, n + 4) for _ in range(n)],
        }
        orders = {"forward": list(range(n)), "reverse": list(reversed(range(n)))}
        shuffled = list(range(n)); rng.shuffle(shuffled); orders["shuffled"] = shuffled
        for pattern, predictions in patterns.items():
            for order_name, order in orders.items():
                # Height1 exercises long horizontal chains; mixed heights exercise
                # ascent, descent and repeated growth of the head tower.
                for height_name in ["unit", "mixed"]:
                    heights = [1] * n if height_name == "unit" else [1 + rng.randrange(6) for _ in range(n)]
                    for model in ["offline", "online", "dirty"]:
                        result.append(dict(id=f"n{n}-{pattern}-{order_name}-{height_name}-{model}",
                                           model=model, predictions=predictions, order=order,
                                           heights=heights, trace=True))
    # Extra larger small traces with geometric heights, tied/signed ranks and
    # shuffled insertion order. They are unrelated to the frozen primary seeds.
    for trial in range(12):
        n = rng.randrange(65, 194)
        predictions = [rng.randrange(-n, n) for _ in range(n)]
        order = list(range(n)); rng.shuffle(order)
        heights = []
        for _ in range(n):
            h = 1
            while rng.random() < .5: h += 1
            heights.append(h)
        for model in ["offline", "online", "dirty"]:
            result.append(dict(id=f"random{trial}-{model}", model=model,
                               predictions=predictions, order=order, heights=heights, trace=True))
    return result


def load_oracle():
    for name, digest in PINS.items():
        if sha(UPSTREAM / name) != digest:
            raise RuntimeError(f"pinned oracle changed: {name}")
    os.environ.setdefault("MPLBACKEND", "Agg")
    os.environ.setdefault("MPLCONFIGDIR", str(HERE / "build" / "mpl-cache"))
    sys.path[:0] = [str(HERE.parent / "deps"), str(UPSTREAM)]
    return importlib.import_module("skiplist"), importlib.import_module("sorting")


def oracle(case, skiplist, sorting):
    heights = iter(case["heights"])
    consumed = 0
    digest = 14695981039346656037
    inserted, clean, dirty = [], [], []
    original_compare = skiplist.SkipList.compare
    original_insert = skiplist.SkipList.insertNextTo

    def sample_height(self):
        nonlocal consumed
        consumed += 1
        return next(heights)

    def compare(self, a, b, is_dirty=False, **kwargs):
        nonlocal digest
        if "dirty" in kwargs: is_dirty = kwargs["dirty"]
        value = original_compare(self, a, b, is_dirty)
        left = -1 if a == -math.inf else int(a)
        right = -1 if b == -math.inf else int(b)
        sign = int(value > 0) - int(value < 0)
        for word in [int(is_dirty), left, right, sign, int(left < 0 or right < 0)]:
            for byte in (int(word) & MASK).to_bytes(8, "little"):
                digest = ((digest ^ byte) * 1099511628211) & MASK
        return value

    def insert(self, value, predecessor):
        node = original_insert(self, value, predecessor)
        inserted.append(int(value)); clean.append(int(self.countComps)); dirty.append(int(self.countDirtyComps))
        return node

    def shuffle(values):
        # Both released wrappers start from canonical key order. Replace their
        # random permutation, not the algorithm itself.
        copy = list(values)
        for i, index in enumerate(case["order"]): values[i] = copy[index]

    predictions = [(rank, key) for key, rank in enumerate(case["predictions"])]
    function = {"offline": sorting.sortSL, "online": sorting.sortOSL, "dirty": sorting.sortDC}[case["model"]]
    with patch.object(skiplist.SkipList, "sampleHeight", sample_height), \
         patch.object(skiplist.SkipList, "compare", compare), \
         patch.object(skiplist.SkipList, "insertNextTo", insert), \
         patch.object(sorting.np.random, "shuffle", shuffle):
        sl = function(predictions)
    vals, observed_heights = sl.getValsHeights()
    n = len(case["predictions"])
    if list(vals) != list(range(n)) or consumed != n:
        raise RuntimeError("upstream oracle sorting/height failure")
    heights_by_key = dict(zip(inserted, case["heights"]))
    if observed_heights != [heights_by_key[key] for key in vals]:
        raise RuntimeError("upstream height assignment mismatch")
    return dict(clean_comparisons=int(sl.countComps), dirty_comparisons=int(sl.countDirtyComps),
                comparison_trace_fnv1a64=str(digest), insertion_order=inserted,
                clean_by_insert=clean, dirty_by_insert=dirty,
                height_count=consumed, sorted_ok=True)


def run_engine(binary, records):
    data = "".join(canonical(record) + "\n" for record in records)
    process = subprocess.run([str(binary)], input=data, text=True, capture_output=True)
    outputs = [json.loads(line) for line in process.stdout.splitlines()]
    return process, outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", default="clang++")
    parser.add_argument("--skip-sanitizer", action="store_true")
    parser.add_argument("--output", type=Path, default=HERE / "engine_preflight.json")
    args = parser.parse_args()
    build = HERE / "build"; build.mkdir(exist_ok=True)
    report = {"status": "running", "scope": "small differential correctness/count tests only; no primary experiment",
              "upstream_commit": PIN, "oracle_source_sha256": PINS,
              "engine_source_sha256": sha(HERE / "replication.cpp"),
              "preflight_source_sha256": sha(__file__), "test_seed": 20261106,
              "comparison_trace_hash": "FNV1a64, five little-endian uint64 words per call: dirty,left,right,sign,free; -1 denotes head",
              "independence": "C++ implementation is independent; unchanged released Python sorting functions are used only as a small-case differential oracle. Explicit height/permutation providers and observation hooks replace randomness, not algorithm logic.",
              "compilations": [], "failures": [], "cases": []}
    try:
        records = cases()
        report["input_sha256"] = hashlib.sha256(canonical(records).encode()).hexdigest()
        skiplist, sorting = load_oracle()
        expected = [oracle(case, skiplist, sorting) for case in records]
        configs = [("optimized", ["-O3", "-DNDEBUG"])]
        if not args.skip_sanitizer:
            configs.append(("asan_ubsan", ["-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer"]))
        version = subprocess.run([args.compiler, "--version"], text=True, capture_output=True, check=True).stdout
        report["compiler_version"] = version
        for label, flags in configs:
            binary = build / ("replication" if label == "optimized" else "replication_sanitized")
            command = [args.compiler, "-std=c++17", "-Wall", "-Wextra", "-Wpedantic", *flags,
                       str(HERE / "replication.cpp"), "-o", str(binary)]
            compiled = subprocess.run(command, text=True, capture_output=True)
            report["compilations"].append(dict(label=label, command=command, exit_code=compiled.returncode,
                                                stdout=compiled.stdout, stderr=compiled.stderr))
            if compiled.returncode: raise RuntimeError(f"{label} compilation failed")
            process, outputs = run_engine(binary, records)
            if process.returncode or len(outputs) != len(records):
                report["failures"].append(dict(configuration=label, exit_code=process.returncode,
                                               stderr=process.stderr, outputs=outputs))
                raise RuntimeError(f"{label} engine batch failed")
            for case, want, got in zip(records, expected, outputs):
                differences = {key: {"expected": value, "actual": got.get(key)}
                               for key, value in want.items() if got.get(key) != value}
                if got.get("status") != "ok" or got.get("id") != case["id"]:
                    differences["identity_or_status"] = got
                if differences:
                    report["failures"].append(dict(configuration=label, case=case, differences=differences))
                if label == "optimized":
                    report["cases"].append(dict(id=case["id"], n=len(case["predictions"]), model=case["model"],
                                               clean=got.get("clean_comparisons"), dirty=got.get("dirty_comparisons"),
                                               trace=got.get("comparison_trace_fnv1a64"), passed=not differences))
            if report["failures"]: raise RuntimeError("differential mismatch")
        analytic_records = [dict(id=f"bh{n}", model="bh_analytic", n=n) for n in [1, 2, 3, 17, 64, 1000]]
        process, outputs = run_engine(build / "replication", analytic_records)
        if process.returncode: raise RuntimeError("analytic reference failed")
        analytic = []
        for record, got in zip(analytic_records, outputs):
            want = float(sorting.countCompsBheapSort(record["n"]))
            passed = math.isclose(want, got["clean_comparisons"], rel_tol=1e-14, abs_tol=1e-12)
            analytic.append(dict(n=record["n"], original=want, independent=got["clean_comparisons"], passed=passed))
            if not passed: raise RuntimeError("analytic reference mismatch")
        bad = [dict(id="zero-height", model="online", predictions=[0], order=[0], heights=[0]),
               dict(id="duplicate-order", model="online", predictions=[0, 1], order=[0, 0], heights=[1, 1]),
               dict(id="bad-size", model="offline", predictions=[0, 1], heights=[1]),
               dict(id="empty", model="offline", predictions=[], heights=[])]
        process, outputs = run_engine(build / "replication", bad)
        if process.returncode != 2 or len(outputs) != len(bad) or any(o.get("status") != "error" for o in outputs):
            raise RuntimeError("malformed input did not fail closed")
        report["malformed_inputs"] = dict(passed=True, records=outputs)
        report["analytic_reference"] = analytic
        # The optional CSV export must agree with the JSONL count values.
        process = subprocess.run([str(build / "replication"), "--format", "csv"],
                                 input=canonical(records[-1]) + "\n", text=True, capture_output=True, check=True)
        row = next(csv.DictReader(io.StringIO(process.stdout)))
        if int(row["clean_comparisons"]) != expected[-1]["clean_comparisons"]:
            raise RuntimeError("CSV export mismatch")
        report.update(status="passed", differential_cases=len(records),
                      ordinal_insertions_per_configuration=sum(len(c["predictions"]) for c in records),
                      configurations=[label for label, _ in configs],
                      clean_dirty_cumulative_and_full_comparison_trace_parity=True,
                      sorted_output_and_explicit_height_assignment=True,
                      primary_experiments_run=False)
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = repr(exc)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report.get(key) for key in ["status", "differential_cases", "ordinal_insertions_per_configuration", "error"]}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
