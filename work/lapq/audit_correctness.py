#!/usr/bin/env python3
"""Bounded differential audit of pinned, unmodified OnlineSL source.

Only source definitions are AST-loaded, avoiding plotting/import side effects.
The original queue methods and numpy height sampler execute unchanged.
"""
import ast
from collections import Counter
import hashlib
import heapq
import json
from pathlib import Path
import random
import signal
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "deps"))
import numpy as np
from sortedcontainers import SortedList

PINNED_SHA = "3915cc261e87949b2b0aa724673c2227492d1b08"
SOURCE = HERE / "upstream" / "skiplist.py"
raw = SOURCE.read_text()
parsed = ast.parse(raw, filename=str(SOURCE))
names = {"Node", "damagedCompare", "SkipList", "OnlineSL"}
selected = [node for node in parsed.body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
assert {node.name for node in selected} == names
namespace = {"np": np, "SortedList": SortedList}
exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), namespace)
OnlineSL = namespace["OnlineSL"]


class TraceTimeout(Exception):
    pass


def alarm_handler(signum, frame):
    raise TraceTimeout("operation exceeded 0.25-second trace limit")


signal.signal(signal.SIGALRM, alarm_handler)
CALLS = 0
CALL_LIMIT = 1900


def reachable_state(queue):
    values, identities = [], set()
    node = queue.sl.head.getNext()
    while node is not None:
        if id(node) in identities:
            return values, identities, "cycle in level-0 chain"
        identities.add(id(node))
        values.append(int(node.value))
        node = node.getNext()
        if len(values) > 200:
            return values, identities, "unexpected level-0 length"
    return values, identities, None


def run_trace(trace, height_seed, keep_steps=False, check_state=True):
    global CALLS
    CALLS += 1
    if CALLS > CALL_LIMIT:
        raise RuntimeError("audit trace limit exceeded")
    np.random.seed(height_seed)
    queue, reference = OnlineSL(), []
    steps, first_dangling = [], None
    signal.setitimer(signal.ITIMER_REAL, 0.25)
    try:
        for index, operation in enumerate(trace):
            observed, expected = None, None
            if operation[0] == "insert":
                _, value, prediction = operation
                heapq.heappush(reference, value)
                queue.insert(value, prediction)
            else:
                if not reference:
                    return {"status": "invalid_trace"}
                expected = heapq.heappop(reference)
                if queue.isEmpty():
                    return {"status": "failure", "kind": "premature_empty",
                            "operation_index": index, "expected": expected,
                            "steps": steps}
                observed = queue.extractMin()
                if observed != expected:
                    return {"status": "failure", "kind": "wrong_extract",
                            "operation_index": index, "expected": expected,
                            "observed": int(observed), "steps": steps}
            values, identities, structural = reachable_state(queue)
            dangling = sorted(int(value) for value in queue.valRank
                              if id(queue.sl.nodes[value]) not in identities)
            if dangling and first_dangling is None:
                first_dangling = {"operation_index": index, "values": dangling}
            step = {"operation": operation, "observed": observed,
                    "expected": expected, "reachable_values": values,
                    "expected_values": sorted(reference),
                    "clean_comparisons": int(queue.countComps),
                    "dangling_hint_values": dangling}
            if keep_steps:
                steps.append(step)
            if check_state and (structural or values != sorted(reference)):
                return {"status": "failure", "kind": structural or "queue_multiset_mismatch",
                        "operation_index": index, "actual": values,
                        "expected": sorted(reference), "first_dangling": first_dangling,
                        "steps": steps}
            if queue.isEmpty() != (not reference):
                return {"status": "failure", "kind": "wrong_empty_status",
                        "operation_index": index, "steps": steps}
        return {"status": "passed", "first_dangling": first_dangling, "steps": steps}
    except Exception as error:
        return {"status": "failure", "kind": type(error).__name__,
                "message": str(error), "operation_index": index,
                "first_dangling": first_dangling, "steps": steps}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def valid_trace(trace):
    size = 0
    for operation in trace:
        size += 1 if operation[0] == "insert" else -1
        if size < 0:
            return False
    return True


def minimize(trace, height_seed):
    # Greedy deletion; preserves a concrete failure, not a global minimality claim.
    current = list(trace)
    changed = True
    while changed:
        changed = False
        for index in range(len(current)):
            trial = current[:index] + current[index + 1:]
            if trial and valid_trace(trial) and run_trace(trial, height_seed)["status"] == "failure":
                current = trial
                changed = True
                break
    return current, run_trace(current, height_seed, keep_steps=True)


def generate_trace(seed, mode):
    rng = random.Random(seed)
    training_rng = random.Random(seed + 1000000)
    training = SortedList(training_rng.randrange(20) for _ in range(16))
    trace, reference, next_unique, last_pop = [], [], 0, 0
    for _ in range(32):
        if not reference or rng.random() < 0.64:
            if mode.startswith("distinct"):
                # Unique across the entire trace, including removed elements.
                value = (next_unique * 37) % 1009
                next_unique += 1
            elif mode.startswith("monotone_"):
                # Dijkstra-compatible insertion floor, including zero edges.
                value = last_pop + rng.randrange(4)
            else:
                value = rng.randrange(6)
            if mode.endswith("cdf_training"):
                prediction = training.bisect_left(value)
            elif mode.endswith("cdf_identity"):
                prediction = value
            elif mode.endswith("cdf_bins"):
                prediction = value // 2
            elif mode.endswith("fixed_nonmonotone"):
                prediction = [3, 1, 0, 2, 1, 3][value % 6]
            elif mode.endswith("zero"):
                prediction = 0
            elif mode.endswith("constant"):
                prediction = 7
            else:
                prediction = rng.randrange(4)
            trace.append(["insert", value, prediction])
            heapq.heappush(reference, value)
        else:
            trace.append(["extract"])
            last_pop = heapq.heappop(reference)
    while reference:
        trace.append(["extract"])
        heapq.heappop(reference)
    return trace


def main():
    directed = [
        ["insert", 0, 0], ["insert", 0, 0], ["extract"], ["insert", 0, 1],
        ["extract"], ["extract"],
    ]
    directed_result = run_trace(directed, 0, keep_steps=True)
    directed_public = run_trace(directed, 0, keep_steps=True, check_state=False)
    modes = ["distinct_zero", "distinct_collisions", "duplicate_zero",
             "duplicate_constant", "duplicate_collisions", "monotone_collisions",
             "duplicate_cdf_identity", "duplicate_cdf_bins",
             "monotone_cdf_identity", "monotone_cdf_bins",
             "duplicate_cdf_training", "monotone_cdf_training",
             "duplicate_fixed_nonmonotone"]
    results, first_failures = {}, {}
    for mode in modes:
        counts = Counter()
        dangling_only = 0
        for seed in range(125):
            trace = generate_trace(seed, mode)
            result = run_trace(trace, seed)
            counts[result["status"]] += 1
            if result.get("first_dangling"):
                dangling_only += 1
            if result["status"] == "failure" and mode not in first_failures:
                first_failures[mode] = {"height_seed": seed, "trace": trace,
                                        "initial_failure": result}
        results[mode] = {"traces": 125, "counts": dict(counts),
                         "traces_observing_dangling_hint": dangling_only}
    minimized = {}
    if directed_result["status"] == "failure":
        trace, result = minimize(directed, 0)
        minimized["directed_duplicate"] = {"height_seed": 0, "trace": trace, "result": result}
    for mode, failure in first_failures.items():
        trace, result = minimize(failure["trace"][:failure["initial_failure"]["operation_index"] + 1],
                                 failure["height_seed"])
        minimized[mode] = {"height_seed": failure["height_seed"], "trace": trace, "result": result}
    remapping_checks = []
    for seed in range(20):
        mode = "distinct_collisions" if seed < 10 else "monotone_cdf_training"
        trace = generate_trace(seed, mode)
        transformed = [[op[0], op[1], 7 * op[2] + 11] if op[0] == "insert" else op
                       for op in trace]
        original_result = run_trace(trace, seed, keep_steps=True)
        transformed_result = run_trace(transformed, seed, keep_steps=True)
        projection = lambda result: [
            {key: value for key, value in step.items() if key != "operation"}
            for step in result["steps"]]
        matches = original_result["status"] == transformed_result["status"] == "passed" \
            and projection(original_result) == projection(transformed_result)
        assert matches, (seed, mode)
        remapping_checks.append({"seed": seed, "mode": mode,
                                 "same_per_operation_clean_comparisons_and_queue_state": matches})
    output = {
        "upstream_commit": PINNED_SHA,
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "numpy_version": np.__version__,
        "loading": "AST-load original Node, damagedCompare, SkipList, OnlineSL; no method modifications",
        "oracle": "stdlib heapq plus sorted level-0 reachable multiset and emptiness checks",
        "height_sampling": "original np.random.geometric(0.5), reseeded for every trace",
        "total_executed_traces_including_minimization": CALLS,
        "random_modes": results,
        "directed_case": {"height_seed": 0, "trace": directed, "result": directed_result},
        "directed_public_interface_continuation": directed_public,
        "minimized_failures": minimized,
        "strict_rank_remapping": {"transformation": "r -> 7*r+11", "checks": remapping_checks},
        "scope": [
            "Duplicate-key cases violate the paper's distinct-key premise and do not refute that theorem.",
            "The original Dijkstra wrapper uses numeric distances as keys and can generate duplicate distances.",
            "A multiset mismatch is a queue-state failure; the directed continuation demonstrates missing extraction.",
            "Greedy minimization establishes local deletion minimality at the recorded height seed, not global minimality.",
            "Passing finite random tests is not a correctness proof.",
        ],
    }
    destination = HERE / "audit_correctness_results.json"
    destination.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"traces": CALLS, "modes": results, "minimized": minimized}, indent=2))
    print("Saved", destination)


if __name__ == "__main__":
    main()
