#!/usr/bin/env python3
"""Counter-free paired LAPQ timing and separate memory workers.

Planning and correctness checks do not launch performance experiments. The
``run`` subcommand is the sole timing runner and requires --approved-run.
Each paired block runs in a fresh subprocess. Graph loading/imports and exact
validation are outside query timers. All kernels use the same distance-key
LIFO routing policy and remove empty routing buckets.
"""
import argparse
from collections import Counter
import gc
import hashlib
import heapq
import importlib
import json
import math
import os
from pathlib import Path
import platform
import random
import resource
import subprocess
import sys
import time
import traceback
import tracemalloc

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "deps"))
from sortedcontainers import SortedList

METHODS = ["heapq_native", "heapq_shared", "sortedlist_native", "skiplist",
           "original_exact", "original_linear", "original_hist64",
           "bitmap_exact", "bitmap_linear", "bitmap_hist64", "veb_linear", "compact_linear"]
ADVISOR_NAMES = ["exact", "linear", "hist64"]


def digest(distance):
    data = "\n".join(float(value).hex() for value in distance).encode()
    return hashlib.sha256(data).hexdigest()


def native_heap(graph, source, collect_keys=False):
    """Native heapq baseline; scalar priorities, common per-distance LIFO ties."""
    distance = [math.inf] * graph.n
    distance[source] = 0
    pending, queue, training_keys = {0: [source]}, [0], []
    while queue:
        cost = heapq.heappop(queue)
        bucket = pending[cost]
        vertex = bucket.pop()
        if not bucket:
            del pending[cost]
        if cost > distance[vertex]:
            continue
        for target, weight in graph.adj[vertex]:
            candidate = cost + weight
            if candidate < distance[target]:
                distance[target] = candidate
                pending.setdefault(candidate, []).append(target)
                heapq.heappush(queue, candidate)
                if collect_keys:
                    training_keys.append(candidate)
    return distance, pending, queue, training_keys


def native_sortedlist(graph, source):
    distance = [math.inf] * graph.n
    distance[source] = 0
    pending, queue = {0: [source]}, SortedList([0])
    while queue:
        cost = queue.pop(0)
        bucket = pending[cost]
        vertex = bucket.pop()
        if not bucket:
            del pending[cost]
        if cost > distance[vertex]:
            continue
        for target, weight in graph.adj[vertex]:
            candidate = cost + weight
            if candidate < distance[target]:
                distance[target] = candidate
                pending.setdefault(candidate, []).append(target)
                queue.add(candidate)
    return distance, pending, queue, None


def shared_kernel(graph, source, queue, advisor):
    distance = [math.inf] * graph.n
    distance[source] = 0
    pending = {0: [source]}
    queue.insert(0, 0)
    while not queue.isEmpty():
        cost = queue.extractMin()
        bucket = pending[cost]
        vertex = bucket.pop()
        if not bucket:
            del pending[cost]
        if cost > distance[vertex]:
            continue
        for target, weight in graph.adj[vertex]:
            candidate = cost + weight
            if candidate < distance[target]:
                distance[target] = candidate
                pending.setdefault(candidate, []).append(target)
                queue.insert(candidate, 0 if advisor is None else advisor(candidate))
    return distance, pending, queue, None


def independent_oracle(graph, source):
    """Different tie policy and tuple heap; no tested queue or routing map."""
    distance = [math.inf] * graph.n
    distance[source] = 0
    queue = [(0, source)]
    while queue:
        cost, vertex = heapq.heappop(queue)
        if cost != distance[vertex]:
            continue
        for target, weight in graph.adj[vertex]:
            candidate = cost + weight
            if candidate < distance[target]:
                distance[target] = candidate
                heapq.heappush(queue, (candidate, target))
    return distance


class Advisor:
    def __init__(self, name, keys):
        self.name, self.n = name, len(keys)
        self.low = min(keys) if keys and name != "exact" else 0
        self.high = max(keys) if keys and name != "exact" else 0
        self.width = self.high - self.low
        self.data = None
        if name == "exact":
            self.data = SortedList(keys)
        elif name == "hist64":
            counts = [0] * 64
            for value in keys:
                index = 0 if not self.width else min(63, int((value - self.low) * 64 / self.width))
                counts[index] += 1
            self.data = [0]
            for count in counts:
                self.data.append(self.data[-1] + count)
        elif name != "linear":
            raise ValueError(name)

    def __call__(self, value):
        if self.name == "exact":
            return self.data.bisect_left(value)
        if not self.width or value <= self.low:
            return 0
        if value >= self.high:
            return self.n
        fraction = (value - self.low) / self.width
        if self.name == "linear":
            return int(fraction * self.n)
        position = fraction * 64
        index = min(63, int(position))
        return int(self.data[index] + (position-index) * (self.data[index+1]-self.data[index]))

    def metadata(self):
        return {"name": self.name, "rank_limit_inclusive": self.n,
                "retained_payload_slots": len(self.data) if self.data is not None else 0,
                "scalar_fields": ["n", "low", "high", "width"],
                "payload_is_not_total_queue_memory": True}


class CheckedQueue:
    """Only used by untimed validation; timed runs use raw queue classes."""
    def __init__(self, queue):
        self.queue, self.inserts, self.extracts = queue, 0, 0
    def insert(self, *args):
        self.queue.insert(*args)
        self.inserts += 1
    def extractMin(self):
        answer = self.queue.extractMin()
        self.extracts += 1
        return answer
    def isEmpty(self):
        return self.queue.isEmpty()


def method_parts(method):
    if method == "heapq_shared":
        return "heapq", None
    if method in ("skiplist", "radix"):
        return method, None
    prefix, advisor = method.split("_", 1)
    kinds = {"original": "original_online", "bitmap": "bitmap_online", "veb": "veb_online",
             "compact": "compact_online"}
    return kinds[prefix], advisor


def make_method_queue(method, seed, advisors, checked=False):
    from queues import make_queue
    kind, advisor_name = method_parts(method)
    advisor = advisors.get(advisor_name)
    queue = make_queue(kind, seed=seed, count=False,
                       rank_limit=advisor.n if advisor is not None else 0)
    return (CheckedQueue(queue) if checked else queue), advisor


def execute(graph, source, method, seed, advisors, checked=False):
    if method == "heapq_native":
        return native_heap(graph, source)
    if method == "sortedlist_native":
        return native_sortedlist(graph, source)
    queue, advisor = make_method_queue(method, seed, advisors, checked)
    return shared_kernel(graph, source, queue, advisor)


def verify(result, expected):
    distance, pending, queue, _ = result
    if distance != expected:
        differences = [(i, actual, wanted) for i, (actual, wanted) in enumerate(zip(distance, expected))
                       if actual != wanted]
        raise AssertionError("distance mismatch: " + repr(differences[:8]))
    if pending:
        raise AssertionError("undrained vertex-routing buckets")
    if isinstance(queue, CheckedQueue):
        if queue.inserts != queue.extracts or not queue.isEmpty():
            raise AssertionError("queue insertion/extraction drain mismatch")
        return {"insertions": queue.inserts, "extractions": queue.extracts,
                "fully_drained": True, "stats": queue.queue.stats()}
    if queue:
        # Raw list and SortedList baselines return their drained containers.
        if isinstance(queue, (list, SortedList)):
            raise AssertionError("undrained native queue")
        if not queue.isEmpty():
            raise AssertionError("undrained queue")
    return {"fully_drained": True}


def get_graphs(spec):
    from workloads import load
    query_graph = load(spec["workload"])
    training_name = spec.get("training_workload", spec["workload"])
    training_graph = query_graph if training_name == spec["workload"] else load(training_name)
    return query_graph, training_graph


def prepare_queue_runtime():
    # Equalize dependency loading across native and adapted queues. Lazy AST
    # class loading must also precede memory accounting and timing.
    for name in ("queues", "indexes", "veb_index", "radix_queue"):
        importlib.import_module(name)
    importlib.import_module("queues").original_classes(False)


def timed_call(function):
    wall, cpu = time.perf_counter_ns(), time.process_time_ns()
    value = function()
    return value, {"wall_ns": time.perf_counter_ns()-wall,
                   "cpu_ns": time.process_time_ns()-cpu}


def environment():
    executed_paths = [HERE/name for name in ("benchmark.py", "queues.py", "indexes.py", "veb_index.py",
                                             "radix_queue.py", "workloads.py")]
    executed_paths += [HERE.parent / "data_loading.py", HERE.parent / "upstream" / "skiplist.py"]
    return {"python": sys.version, "executable": sys.executable,
            "platform": platform.platform(), "machine": platform.machine(),
            "processor": platform.processor(), "cpu_count_visible": os.cpu_count(),
            "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
            "process_id": os.getpid(), "gc_enabled_before_block": gc.isenabled(),
            "hardware_verified_by_coordinator": {"model": "MacBookAir10,1", "chip": "Apple M1",
                "physical_memory_bytes": 8589934592, "total_cores": 8, "performance_cores": 4,
                "efficiency_cores": 4, "macos_version": "14.3.1"},
            "host_control": "CPU affinity/frequency not controlled; no dedicated or isolated-machine claim. Other research agents idle during timing, but OS/user activity can remain.",
            "argv": sys.argv, "timer": "perf_counter_ns and process_time_ns",
            "source_sha256": {str(p.relative_to(HERE.parent)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in executed_paths}}


def advisor_names(methods):
    return sorted({method_parts(method)[1] for method in methods
                   if method not in ("heapq_native", "sortedlist_native", "heapq_shared", "skiplist", "radix")})


def training_and_advisors(graph, source, methods, expected=None):
    needed = advisor_names(methods)
    if not needed:
        return {}, {"not_required": True, "advisor_builds": {},
                    "cost_scope": "This method uses no training or advisor."}
    if expected is None:
        expected = independent_oracle(graph, source)
    gc.collect()
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        result, training_time = timed_call(lambda: native_heap(graph, source, collect_keys=True))
        verify(result, expected)
        keys = result[3]
        advisors, builds = {}, {}
        for name in needed:
            advisor, duration = timed_call(lambda name=name: Advisor(name, keys))
            advisors[name] = advisor
            builds[name] = {**duration, **advisor.metadata()}
        info = {"collecting_dijkstra": training_time, "key_count": len(keys),
                "advisor_builds": builds, "distance_digest": digest(expected),
                "cost_scope": "One collecting native-heap run plus each advisor build; validation/load excluded."}
    finally:
        if was_enabled:
            gc.enable()
    return advisors, info


def timing_worker(spec):
    prepare_queue_runtime()
    graph, training_graph = get_graphs(spec)
    expected = independent_oracle(graph, spec["query_source"])
    advisors, training = training_and_advisors(training_graph, spec["train_source"], spec["methods"])
    order = list(spec["methods"])
    random.Random(spec["method_order_seed"]).shuffle(order)
    records = []
    for method in order:
        record = {"method": method, "status": "error"}
        try:
            # The verified warmup also checks insertion/extraction counts.
            warmup = execute(graph, spec["query_source"], method, spec["queue_seed"], advisors, checked=True)
            record["untimed_validation"] = verify(warmup, expected)
            del warmup
            gc.collect()
            importlib.import_module("numpy").random.seed(spec["queue_seed"])
            was_enabled = gc.isenabled()
            gc.disable()
            try:
                result, duration = timed_call(lambda: execute(graph, spec["query_source"], method,
                                                             None, advisors))
            finally:
                if was_enabled:
                    gc.enable()
            # All correctness and introspection occur after timers stop.
            verify(result, expected)
            record.update(status="ok", query_wall_ns=duration["wall_ns"], query_cpu_ns=duration["cpu_ns"],
                          wall_cpu_ratio=duration["wall_ns"]/max(1, duration["cpu_ns"]),
                          distance_digest=digest(result[0]))
            if not isinstance(result[2], (list, SortedList)):
                record["post_query_queue_stats"] = result[2].stats()
            start_wall, start_cpu = time.perf_counter_ns(), time.process_time_ns()
            del result
            gc.collect()
            record["forced_cleanup_wall_ns"] = time.perf_counter_ns()-start_wall
            record["forced_cleanup_cpu_ns"] = time.process_time_ns()-start_cpu
        except Exception as error:
            record.update(error_type=type(error).__name__, error=str(error), traceback=traceback.format_exc())
            gc.collect()
        records.append(record)
    return {"kind": "timing_block", "spec": spec, "environment": environment(),
            "training": training, "method_order": order, "records": records,
            "protocol": "Fresh process per paired block; one verified method-specific warmup; query includes queue creation and advisor calls; NumPy seed reset outside timer; GC disabled during query; forced cleanup separately reported, not a deployment-GC estimate. All required advisors remain resident across this paired block."}


def rss_bytes():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def memory_worker(spec):
    prepare_queue_runtime()
    graph, training_graph = get_graphs(spec)
    expected_digest = digest(independent_oracle(graph, spec["query_source"]))
    method = spec["method"]
    training_expected = independent_oracle(training_graph, spec["train_source"]) if advisor_names([method]) else None
    gc.collect()
    baseline = rss_bytes()
    tracemalloc.start()
    advisors, training = training_and_advisors(training_graph, spec["train_source"], [method], expected=training_expected)
    del training_expected
    gc.collect()
    model_current, construction_peak = tracemalloc.get_traced_memory()
    rss_after_model = rss_bytes()
    tracemalloc.reset_peak()
    result = execute(graph, spec["query_source"], method, spec["queue_seed"], advisors)
    current, peak = tracemalloc.get_traced_memory()
    rss_after_query = rss_bytes()
    # Snapshot numbers above exclude subsequent digest/stats allocations.
    if result[1] or digest(result[0]) != expected_digest:
        raise AssertionError("memory-run distance/drain check failed")
    queue_stats = result[2].stats() if not isinstance(result[2], (list, SortedList)) else {"live": len(result[2])}
    del result
    gc.collect()
    after_cleanup = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    return {"kind": "memory", "spec": spec, "environment": environment(), "status": "ok",
            "rss_highwater_after_graph_load_and_oracle_bytes": baseline,
            "rss_highwater_after_advisor_build_bytes": rss_after_model,
            "rss_highwater_after_query_bytes": rss_after_query,
            "rss_highwater_growth_over_graph_baseline_bytes": max(0, rss_after_query-baseline),
            "rss_highwater_growth_after_advisor_build_bytes": max(0, rss_after_query-rss_after_model),
            "python_traced_model_retained_bytes": model_current,
            "python_traced_training_and_build_peak_bytes": construction_peak,
            "python_traced_query_peak_including_model_bytes": peak,
            "python_traced_query_peak_increment_bytes": max(0, peak-model_current),
            "python_traced_post_query_retained_bytes": current,
            "python_traced_after_query_cleanup_bytes": after_cleanup,
            "post_query_queue_stats": queue_stats, "training": training,
            "memory_scope": "Graph and precomputed oracle excluded from tracemalloc; training/advisor and query Python allocations included. RSS is lifetime high-water, not current RSS; differences are high-water growth, not exact component memory. Allocator retention, training temporaries, imports, and graph loading can mask later RSS growth. Each method uses a separate process. Model-retained measurement includes small training metadata."}


def selfcheck():
    prepare_queue_runtime()
    class Graph:
        adj = [[(1, 0), (2, 0), (3, 4)], [(3, 1)], [(3, 1), (4, 2)], [(4, 0)], []]
        n, m = 5, 7
    graph = Graph()
    expected = independent_oracle(graph, 0)
    keys = native_heap(graph, 0, True)[3]
    advisors = {name: Advisor(name, keys) for name in ADVISOR_NAMES}
    results = []
    for seed in (17, 29, 43):
        for method in METHODS + ["radix"]:
            answer = execute(graph, 0, method, seed, advisors, checked=True)
            checks = verify(answer, expected)
            results.append({"method": method, "seed": seed, "checks": checks})
            del answer
            gc.collect()
    return {"status": "passed", "cases": len(results), "checks": results,
            "scope": "Small zero/tie-rich connected graph; independent oracle; untimed. Workload-sized validation is also required before timing each method."}


def create_plan(args):
    from workloads import workload_catalog, select_training_and_queries
    catalog = workload_catalog()
    catalog_map = catalog if isinstance(catalog, dict) else {entry["name"]: entry for entry in catalog}
    names = args.workloads.split(",")
    methods = args.methods.split(",")
    blocks, memory = [], []
    for name in names:
        metadata = catalog_map.get(name, {})
        splits = select_training_and_queries(name, seed=args.source_seed)
        if args.train_limit:
            splits = splits[:args.train_limit]
        training_name = metadata.get("training_graph", metadata.get("training_workload", metadata.get("train_on", name)))
        for train_index, split in enumerate(splits):
            queries = list(split["queries"])
            if args.first_near_far:
                queries = [next(q for q in queries if q["group"] == group) for group in ("near", "far")]
            if args.query_limit:
                queries = queries[:args.query_limit]
            memory_added = False
            for query_index, query in enumerate(queries):
                repetitions = args.ny_repeats if "ny" in name.lower() or "new_york" in name.lower() else args.repeats
                selected_methods = list(methods)
                if metadata.get("all_weights_int_type", metadata.get("integer_weights")) and "radix" not in selected_methods:
                    selected_methods.append("radix")
                for repeat in range(repetitions):
                    block = {"stage": args.stage, "workload": name, "training_workload": training_name,
                             "train_source": split["train_source"], "query_source": query["source"],
                             "query_group": query["group"], "train_index": train_index,
                             "query_index": query_index, "repeat": repeat,
                             "queue_seed": [17, 29, 43, 61, 79][repeat % 5],
                             "method_order_seed": args.order_seed + len(blocks), "methods": selected_methods}
                    block["block_id"] = hashlib.sha256(json.dumps(block, sort_keys=True).encode()).hexdigest()[:16]
                    blocks.append(block)
                if (args.memory and not memory_added and query["group"] == args.memory_query_group
                        and (not args.memory_first_train_only or train_index == 0)):
                    for method in selected_methods:
                        memory.append({**block, "method": method, "memory_id": f"{name}-{train_index}-{method}"})
                    memory_added = True
    if args.block_limit:
        blocks = blocks[:args.block_limit]
        allowed = {(b["workload"], b["train_source"]) for b in blocks}
        memory = [m for m in memory if (m["workload"], m["train_source"]) in allowed]
    if args.memory_only:
        blocks = []
    plan = {"version": 1, "stage": args.stage, "created_unix_seconds": time.time(), "argv": sys.argv,
            "environment": environment(), "catalog": catalog, "blocks": blocks, "memory": memory,
            "minimum_repeats_small": args.repeats, "minimum_repeats_ny": args.ny_repeats,
            "analysis_units": "Workload and training/query source; queue/time repetitions are nested, not independent graph samples.",
            "planned_source_sha256": environment()["source_sha256"],
            "approval": "Plan creation authorizes no performance run. Root must approve before run --approved-run.",
            "limitations": ["Python implementation study; no independent-core claim or hardware isolation claim.",
                            "Query timing includes prediction lookup and index work, excludes training, validation, imports, graph loading, and forced cleanup.",
                            "Training/build costs and forced cleanup are reported separately.",
                            "Full oracle equality and untimed insertion/extraction drain checks precede each timed method.",
                            "Native and shared-dispatch heap controls separate dispatch overhead.",
                            "All kernels remove empty per-distance routing buckets, a documented departure from artifact retention.",
                            "Counter-free queue classes remove only analytic counter updates; index substitutions are labeled."]}
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2)+"\n")
    print(json.dumps({"plan": str(path), "blocks": len(blocks), "timed_queries": sum(len(b["methods"]) for b in blocks),
                      "memory_workers": len(memory), "status": "prepared, not executed"}))


def run_plan(args):
    if not args.approved_run:
        raise RuntimeError("Performance runs require root approval and --approved-run.")
    path = Path(args.plan)
    plan = json.loads(path.read_text())
    if plan.get("planned_source_sha256") != environment()["source_sha256"]:
        raise RuntimeError("Executed source hashes differ from frozen plan; prepare/review a new plan before running.")
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    # O_EXCL prevents this driver from starting a second local runner.
    lock = HERE / ".timing-runner.lock"
    lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.write(lock_fd, str(os.getpid()).encode())
    os.close(lock_fd)
    try:
        jobs = [("timing", b, b["block_id"]) for b in plan["blocks"]]
        if args.memory:
            jobs += [("memory", m, m["memory_id"]) for m in plan["memory"]]
        for index, (kind, spec, identifier) in enumerate(jobs):
            output = out / f"{kind}-{identifier}.json"
            if output.exists() and args.resume:
                print(f"Existing {index+1}/{len(jobs)} {output.name}", flush=True)
                continue
            spec_path = out / f"spec-{kind}-{identifier}.json"
            spec_path.write_text(json.dumps(spec, indent=2)+"\n")
            command = [sys.executable, str(Path(__file__).resolve()), "worker", "--kind", kind, "--spec", str(spec_path)]
            started = time.time()
            launch_hashes = environment()["source_sha256"]
            try:
                child = subprocess.run(command, capture_output=True, text=True, timeout=args.worker_timeout,
                                       env={**os.environ, "PYTHONHASHSEED": "0"})
                if child.returncode:
                    record = {"kind": kind, "spec": spec, "status": "worker_error", "returncode": child.returncode,
                              "stdout": child.stdout, "stderr": child.stderr, "argv": command}
                else:
                    record = json.loads(child.stdout)
                    record["launcher_argv"] = command
                    if child.stderr:
                        record["worker_stderr"] = child.stderr
            except subprocess.TimeoutExpired as error:
                record = {"kind": kind, "spec": spec, "status": "timeout", "argv": command,
                          "timeout_seconds": args.worker_timeout, "error": str(error)}
            record["launcher_elapsed_seconds"] = time.time()-started
            record["source_sha256_at_launch"] = launch_hashes
            record["source_sha256_at_completion"] = environment()["source_sha256"]
            record["code_unchanged_during_worker"] = record["source_sha256_at_launch"] == record["source_sha256_at_completion"]
            record["plan_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            output.write_text(json.dumps(record, indent=2)+"\n")
            print(f"Completed {index+1}/{len(jobs)} {output.name} {record.get('status', 'recorded')}", flush=True)
    finally:
        lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("selfcheck")
    worker = sub.add_parser("worker")
    worker.add_argument("--kind", choices=["timing", "memory"], required=True)
    worker.add_argument("--spec", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--workloads", default="brussels")
    plan.add_argument("--stage", choices=["feasibility_pilot", "primary", "shift"], default="primary")
    plan.add_argument("--methods", default=",".join(METHODS))
    plan.add_argument("--source-seed", type=int, default=20261105)
    plan.add_argument("--order-seed", type=int, default=41001)
    plan.add_argument("--repeats", type=int, default=5)
    plan.add_argument("--ny-repeats", type=int, default=3)
    plan.add_argument("--train-limit", type=int, default=0)
    plan.add_argument("--query-limit", type=int, default=0)
    plan.add_argument("--first-near-far", action="store_true")
    plan.add_argument("--block-limit", type=int, default=0)
    plan.add_argument("--memory", action="store_true")
    plan.add_argument("--memory-only", action="store_true")
    plan.add_argument("--memory-first-train-only", action="store_true")
    plan.add_argument("--memory-query-group", default="far", choices=["near", "far"])
    plan.add_argument("--output", default=str(HERE / "protocol.json"))
    run = sub.add_parser("run")
    run.add_argument("--plan", required=True)
    run.add_argument("--output-dir", required=True)
    run.add_argument("--approved-run", action="store_true")
    run.add_argument("--memory", action="store_true")
    run.add_argument("--resume", action="store_true")
    run.add_argument("--worker-timeout", type=float, default=1800)
    args = parser.parse_args()
    if args.command == "selfcheck":
        print(json.dumps(selfcheck(), indent=2))
    elif args.command == "worker":
        spec = json.loads(Path(args.spec).read_text())
        answer = timing_worker(spec) if args.kind == "timing" else memory_worker(spec)
        print(json.dumps(answer))
    elif args.command == "plan":
        create_plan(args)
    else:
        run_plan(args)


if __name__ == "__main__":
    main()
