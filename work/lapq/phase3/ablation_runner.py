#!/usr/bin/env python3
"""Execute the frozen phase-three design in separate sequential subprocesses.

No study runs on import. Main execution requires a passing full-workload trace
gate, a pinned execution manifest and explicit --approved-run. Selfcheck uses
only tiny graphs. The frozen adapters, proposal and phase-two files are read-only.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gc
import hashlib
from importlib import metadata
import json
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
PHASE2 = HERE.parent / 'phase2'
ROOT = HERE.parents[2]
PROTOCOL = HERE / 'ablation_protocol.json'
WORKLOAD_MANIFEST = PHASE2 / 'workload_manifest.json'
GATE_SCRIPT = HERE / 'full_trace_gate.py'
GATE_RESULT = HERE / 'full_trace_gate_results.json'
EXECUTION_MANIFEST = HERE / 'execution_manifest.json'
RESULTS = HERE / 'results'
LOCK = HERE / '.ablation-runner.lock'
sys.path.insert(0, str(PHASE2))
from ablation_queues import METHODS, OCCURRENCE_METHODS, make_ablation_queue
import benchmark as phase2
import numpy as np


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def environment():
    return {'python': sys.version, 'executable': sys.executable,
            'platform': platform.platform(), 'machine': platform.machine(),
            'processor': platform.processor(), 'cpu_count_visible': os.cpu_count(),
            'load_average': list(os.getloadavg()) if hasattr(os, 'getloadavg') else None,
            'process_id': os.getpid(), 'argv': sys.argv,
            'numpy': np.__version__, 'sortedcontainers': metadata.version('sortedcontainers'),
            'host_control': 'Shared host; CPU affinity, frequency, thermal state and OS activity uncontrolled.',
            'timer': 'perf_counter_ns and process_time_ns',
            'gc_thresholds': list(gc.get_threshold())}


def rss_bytes():
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(raw if sys.platform == 'darwin' else raw * 1024)


def source_hashes(paths):
    return {name: sha(ROOT / name) for name in paths}


def verify_hashes(expected):
    observed = source_hashes(expected)
    require(observed == expected, 'A frozen source or input-manifest hash changed')
    return observed


def warm_runtime(methods=METHODS):
    phase2.prepare_queue_runtime()
    # Force NumPy's lazy random subsystem and both bucket paths to initialize
    # before either timing or tracemalloc. Never count this as queue memory.
    np.random.seed(101)
    for method in methods:
        q = make_ablation_queue(method, rank_limit=2, seed=None, count=False)
        q.insert(0, 0)
        q.insert(0, 0)
        q.insert(1, 1)
        require([q.extractMin(), q.extractMin(), q.extractMin()] == [0, 0, 1],
                'runtime warmup failed')
        require(q.isEmpty(), 'runtime warmup did not drain')
        del q
    gc.collect()


def load_case(spec):
    from workloads import load, graph_hash, _distance_hash
    document = json.loads(WORKLOAD_MANIFEST.read_text())
    graph = load(spec['workload'])
    train_name = spec['training_graph']
    training_graph = graph if train_name == spec['workload'] else load(train_name)
    graph_digest = graph_hash(graph)
    require(graph_digest == spec['target_adjacency_sha256'], 'Target adjacency mismatch')
    training_digest = graph_digest if training_graph is graph else graph_hash(training_graph)
    require(training_digest == document['workloads'][train_name]['adjacency_sha256'],
            'Training adjacency mismatch')
    expected = phase2.independent_oracle(graph, spec['query_source'])
    require(_distance_hash(expected) == spec['reference_distance_sha256'],
            'Target reference distances differ from frozen manifest')
    training_expected = phase2.independent_oracle(training_graph, spec['train_source'])
    require(_distance_hash(training_expected) == document['workloads'][train_name]
            ['reference_distances'][str(spec['train_source'])]['sha256'],
            'Training reference distances differ from frozen manifest')
    return graph, training_graph, expected, training_expected


def fit_linear(graph, source, expected):
    """Same phase-two collector and Advisor, with small explicit metadata.

Avoid allocating a giant validation-digest string while memory tracing is on.
Training vectors are compared directly, outside the collection/build clocks.
"""
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        collected, collection_time = phase2.timed_call(
            lambda: phase2.native_heap(graph, source, collect_keys=True))
        phase2.verify(collected, expected)
        keys = collected[3]
        advisor, build_time = phase2.timed_call(lambda: phase2.Advisor('linear', keys))
        info = {'collecting_dijkstra': collection_time, 'key_count': len(keys),
                'advisor_builds': {'linear': {**build_time, **advisor.metadata()}},
                'training_vector_verified': True,
                'cost_scope': 'One common phase-two native_heap trace plus phase-two linear Advisor build; validation outside clocks.'}
        del collected, keys
    finally:
        if was_enabled:
            gc.enable()
    return advisor, info


def execute(graph, source, method, advisor, checked=False):
    q = make_ablation_queue(method, rank_limit=advisor.n, seed=None, count=False)
    if checked:
        q = phase2.CheckedQueue(q)
    return phase2.shared_kernel(graph, source, q, advisor)


def validate_result(result, expected):
    # Exact vectors and routing drain, plus insertion/extraction equality when
    # the untimed checked wrapper is present. No diagnostic work enters clocks.
    return phase2.verify(result, expected)


def _timing_loaded(spec, graph, training_graph, expected, training_expected):
    advisor, training = fit_linear(training_graph, spec['train_source'], training_expected)
    records, reference_work = [], None
    for ordinal, method in enumerate(spec['method_order']):
        record = {'method': method, 'order': ordinal, 'status': 'error',
                  'queue_seed': spec['queue_seed']}
        result = None
        try:
            np.random.seed(spec['queue_seed'])
            warmup = execute(graph, spec['query_source'], method, advisor, checked=True)
            validation = validate_result(warmup, expected)
            record['untimed_validation'] = validation
            if method in OCCURRENCE_METHODS:
                work = (validation['insertions'], validation['extractions'],
                        validation['stats']['max_skiplist_height'])
                if reference_work is None:
                    reference_work = work
                require(work == reference_work, 'Occurrence warmup work/height mismatch')
            del warmup
            gc.collect()
            np.random.seed(spec['queue_seed'])
            was_enabled = gc.isenabled()
            gc.disable()
            try:
                result, duration = phase2.timed_call(
                    lambda: execute(graph, spec['query_source'], method, advisor))
                # Keep collection disabled through validation and the measured
                # explicit cleanup boundary so implicit collection cannot escape
                # into an unrecorded gap between query and cleanup.
                validate_result(result, expected)
                record.update(status='ok', query_wall_ns=duration['wall_ns'],
                              query_cpu_ns=duration['cpu_ns'],
                              wall_cpu_ratio=duration['wall_ns'] / max(1, duration['cpu_ns']),
                              distance_digest=phase2.digest(result[0]),
                              post_query_queue_stats=result[2].stats())
                start_wall, start_cpu = time.perf_counter_ns(), time.process_time_ns()
                del result
                result = None
                collected = gc.collect()
                record['forced_cleanup_wall_ns'] = time.perf_counter_ns() - start_wall
                record['forced_cleanup_cpu_ns'] = time.process_time_ns() - start_cpu
                record['forced_cleanup_collected_objects'] = collected
            finally:
                if was_enabled:
                    gc.enable()
        except Exception as error:
            record.update(status='error', error_type=type(error).__name__,
                          error=str(error), traceback=traceback.format_exc())
            result = None
            gc.collect()
            records.append(record)
            # A failed validation invalidates this paired block; preserve it and
            # stop rather than producing apparently comparable partial timings.
            break
        records.append(record)
    passed = len(records) == len(spec['method_order']) and all(r['status'] == 'ok' for r in records)
    return {'kind': 'timing_block', 'status': 'ok' if passed else 'error',
            'spec': spec, 'method_order': spec['method_order'], 'records': records,
            'training': training, 'environment': environment(),
            'occurrence_warmup_work_parity_passed': passed,
            'scope': 'Fresh process per block; one checked warmup per method; shared kernel, matched outside-timer seed; cyclic GC disabled during query and explicit cleanup; all correctness/stats outside query clocks.'}


def timing_worker(spec):
    warm_runtime()
    return _timing_loaded(spec, *load_case(spec))


def graph_memory_worker(spec):
    warm_runtime()
    return _graph_memory_loaded(spec, *load_case(spec))


def _graph_memory_loaded(spec, graph, training_graph, expected, training_expected):
    original_gc = gc.isenabled()
    gc.collect()
    np.random.seed(spec['queue_seed'])
    rss_baseline = rss_bytes()
    tracemalloc.start()
    try:
        advisor, training = fit_linear(training_graph, spec['train_source'], training_expected)
        del training_expected
        gc.collect()
        model_current, training_peak = tracemalloc.get_traced_memory()
        rss_model = rss_bytes()
        tracemalloc.reset_peak()
        if spec['gc_mode'] == 'enabled':
            gc.enable()
        else:
            gc.disable()
        result = execute(graph, spec['query_source'], spec['method'], advisor)
        gc.disable()  # no automatic collection between completion and pre-GC snapshots
        current, query_peak = tracemalloc.get_traced_memory()
        rss_query = rss_bytes()
        require(result[0] == expected and not result[1] and result[2].isEmpty(),
                'Graph-memory distance/drain mismatch')
        q = result[2]
        del result  # remove distances and routing, keeping only the empty queue
        empty_before_gc = tracemalloc.get_traced_memory()[0]
        collected = gc.collect()
        empty_after_gc = tracemalloc.get_traced_memory()[0]
        queue_stats = q.stats()  # after snapshots: this small report buffer is retained below
        stats_current = tracemalloc.get_traced_memory()[0]
        del q
        gc.collect()
        after_destroy = tracemalloc.get_traced_memory()[0]
        del advisor
        gc.collect()
        after_advisor = tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()
        if original_gc:
            gc.enable()
        else:
            gc.disable()
    return {'kind': 'graph_memory', 'status': 'ok', 'spec': spec,
            'environment': environment(), 'training': training,
            'distance_vector_verified': True, 'fully_drained': True,
            'rss_highwater_after_graph_and_oracle_bytes': rss_baseline,
            'rss_highwater_after_training_bytes': rss_model,
            'rss_highwater_after_query_bytes': rss_query,
            'python_traced_model_retained_bytes': model_current,
            'python_traced_training_and_build_peak_bytes': training_peak,
            'python_traced_query_peak_including_model_bytes': query_peak,
            'python_traced_post_query_retained_bytes': current,
            'python_traced_empty_queue_alive_before_gc_bytes': empty_before_gc,
            'python_traced_empty_queue_alive_after_gc_bytes': empty_after_gc,
            'python_traced_after_queue_destroyed_bytes': after_destroy,
            'python_traced_after_advisor_destroyed_bytes': after_advisor,
            'post_snapshot_stats_buffer_traced_increment_bytes': max(0, stats_current - empty_after_gc),
            'explicit_collection_objects_while_queue_alive': collected,
            'post_query_queue_stats': queue_stats,
            'memory_scope': 'Graph/oracle and prewarmed libraries excluded. Query peak includes advisor, metadata, distances, routing and queue. Empty-queue snapshots drop distances/routing but retain advisor, with automatic GC disabled until explicit collection. After-destruction snapshots retain small training/statistics reports. RSS is lifetime high-water, not exact component memory. Training peak omits large validation digest-string construction.'}


def retention_worker(spec):
    warm_runtime()
    original_gc = gc.isenabled()
    gc.collect()
    np.random.seed(spec['queue_seed'])
    tracemalloc.start()
    try:
        if spec['gc_mode'] == 'enabled':
            gc.enable()
        else:
            gc.disable()
        q = make_ablation_queue(spec['method'], rank_limit=0, seed=None, count=False)
        next_extract = 0
        for value in range(spec['insertions']):
            if value >= spec['max_live']:
                actual = q.extractMin()
                require(actual == next_extract, 'Bounded-live extraction mismatch')
                next_extract += 1
            q.insert(value, 0)
        live_current, live_peak = tracemalloc.get_traced_memory()
        while not q.isEmpty():
            actual = q.extractMin()
            require(actual == next_extract, 'Bounded-live drain mismatch')
            next_extract += 1
        gc.disable()
        require(next_extract == spec['insertions'], 'Bounded-live lost entries')
        empty_before_gc, total_peak = tracemalloc.get_traced_memory()
        collected = gc.collect()
        empty_after_gc = tracemalloc.get_traced_memory()[0]
        queue_stats = q.stats()
        stats_current = tracemalloc.get_traced_memory()[0]
        del q
        gc.collect()
        after_destroy = tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()
        if original_gc:
            gc.enable()
        else:
            gc.disable()
    return {'kind': 'retention', 'status': 'ok', 'spec': spec,
            'environment': environment(), 'all_extractions_correct': True,
            'insertions': spec['insertions'], 'extractions': next_extract,
            'python_traced_current_at_live_limit_bytes': live_current,
            'python_traced_peak_at_live_limit_bytes': live_peak,
            'python_traced_total_peak_bytes': total_peak,
            'python_traced_empty_queue_alive_before_gc_bytes': empty_before_gc,
            'python_traced_empty_queue_alive_after_gc_bytes': empty_after_gc,
            'python_traced_after_queue_destroyed_bytes': after_destroy,
            'post_snapshot_stats_buffer_traced_increment_bytes': max(0, stats_current - empty_after_gc),
            'explicit_collection_objects_while_queue_alive': collected,
            'post_query_queue_stats': queue_stats,
            'memory_scope': 'Unique increasing keys; constant zero rank; occupancy bounded. RNG/classes prewarmed. No timing claim. Empty snapshots precede stats allocation; after-destruction retains small statistics report. Automatic GC disabled between drain and pre-GC snapshot.'}


def build_jobs(protocol):
    jobs = []
    for block in protocol['blocks']:
        identifier = hashlib.sha256(block['block_id'].encode()).hexdigest()[:16]
        jobs.append({'job_id': 'timing-' + identifier, 'kind': 'timing', 'spec': block})
    cases = [c for c in protocol['source_cases'] if c['train_index'] == 0 and c['query_group'] == 'far']
    for case in cases:
        seed = next(b['queue_seed'] for b in protocol['blocks']
                    if b['case_id'] == case['case_id'] and b['repeat'] == 0)
        for method in protocol['methods']:
            for mode in ('enabled', 'disabled_during_query'):
                spec = {**case, 'method': method, 'gc_mode': mode, 'queue_seed': seed}
                jobs.append({'job_id': f"graph_memory-{case['workload']}-{method}-{mode}",
                             'kind': 'graph_memory', 'spec': spec})
    for n in protocol['bounded_live_diagnostic']['insertions']:
        for method in protocol['methods']:
            for mode in ('enabled', 'disabled_during_trace'):
                spec = {'method': method, 'gc_mode': mode, 'insertions': n,
                        'max_live': 128, 'queue_seed': 101, 'prediction': 'constant0',
                        'rank_universe': 1}
                jobs.append({'job_id': f'retention-{n}-{method}-{mode}',
                             'kind': 'retention', 'spec': spec})
    # Preserve the protocol's exact timing order; independent seeded shuffles
    # fix the two subsequent memory stages before any measurements are observed.
    timing = [j for j in jobs if j['kind'] == 'timing']
    graph_memory = [j for j in jobs if j['kind'] == 'graph_memory']
    retention = [j for j in jobs if j['kind'] == 'retention']
    random.Random(protocol['protocol_seed'] ^ 0x293).shuffle(graph_memory)
    random.Random(protocol['protocol_seed'] ^ 0x8A1).shuffle(retention)
    require(len(timing) == 108 and len(graph_memory) == 40 and len(retention) == 30,
            'Frozen job-count mismatch')
    ordered = timing + graph_memory + retention
    require(len({j['job_id'] for j in ordered}) == len(ordered), 'Duplicate planned job IDs')
    return ordered


def prepare_manifest():
    require(not EXECUTION_MANIFEST.exists(), 'Execution manifest exists; preserve it rather than silently replace')
    protocol = json.loads(PROTOCOL.read_text())
    verify_hashes(protocol['source_sha256'])
    require(sha(WORKLOAD_MANIFEST) == protocol['workload_manifest_sha256'], 'Workload manifest changed')
    gate = json.loads(GATE_RESULT.read_text())
    require(gate.get('status') == 'passed', 'Full-workload trace gate must pass first')
    require(gate.get('planned_source_cases') == 24 and gate.get('observed_source_cases') == 24,
            'Full-workload gate coverage mismatch')
    require(gate.get('protocol_sha256') == sha(PROTOCOL), 'Gate used a different protocol')
    verify_hashes(gate['source_sha256'])
    check = json.loads((HERE / 'runner_selfcheck.json').read_text())
    require(check.get('status') == 'passed' and check.get('runner_sha256') == sha(Path(__file__)),
            'Current runner needs a passing selfcheck')
    extra = [HERE / 'ablation_runner.py',
             HERE / 'preflight_results.json', HERE / 'runner_selfcheck.json',
             GATE_SCRIPT, GATE_RESULT, PROTOCOL,
             WORKLOAD_MANIFEST, PHASE2 / 'veb_index.py', PHASE2 / 'radix_queue.py']
    paths = list(dict.fromkeys(list(protocol['source_sha256']) + [str(p.relative_to(ROOT)) for p in extra]))
    manifest = {'schema_version': 1, 'status': 'Frozen execution manifest; measurements not started',
                'created_utc': now(), 'protocol_sha256': sha(PROTOCOL),
                'protocol_path': str(PROTOCOL.relative_to(ROOT)),
                'source_sha256': source_hashes(paths),
                'full_trace_gate': {'script_sha256': sha(GATE_SCRIPT), 'result_sha256': sha(GATE_RESULT),
                                    'coverage': '24 source cases, first planned seed per case, four occurrence variants; not every timing seed'},
                'analysis_boundary': 'Postprocessing analysis is not imported by workers and is excluded from executed-source hash guards; its final version is separately recorded in the analysis summary. Gate/preflight evidence is pinned as an execution prerequisite.',
                'execution_refinements': ['One separate process per job; all timing precedes memory stages.',
                    'Memory modes/seed fixed here where proposal specified policies; graph memory uses repeat-zero case seed, retention seed101.',
                    'Memory training uses the unchanged collector/Advisor but avoids allocating large validation digest strings while tracing.',
                    'Automatic collection stays disabled after query/drain until the pre-GC snapshot and explicit collection.',
                    'Timing failure invalidates the paired block; preserve partial records and stop the sequence for review.'],
                'job_counts': {'timing': 108, 'graph_memory': 40, 'retention': 30},
                'planned_timed_method_records': 540, 'jobs': build_jobs(protocol)}
    for job in manifest['jobs']:
        job['output_name'] = job['job_id'] + '.json'
    atomic_json(EXECUTION_MANIFEST, manifest)
    print(json.dumps({'manifest': str(EXECUTION_MANIFEST), 'jobs': len(manifest['jobs']),
                      'performance_started': False}))


def worker_main(kind, spec_path, manifest_path):
    manifest = json.loads(Path(manifest_path).read_text())
    verify_hashes(manifest['source_sha256'])
    spec = json.loads(Path(spec_path).read_text())
    function = {'timing': timing_worker, 'graph_memory': graph_memory_worker,
                'retention': retention_worker}[kind]
    result = function(spec)
    verify_hashes(manifest['source_sha256'])
    return result


def run_manifest(args):
    require(args.approved_run, 'Review and explicit --approved-run are required')
    require(not (PHASE2 / '.timing-runner.lock').exists(), 'Phase-two runner is active')
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text())
    verify_hashes(manifest['source_sha256'])
    require(sha(PROTOCOL) == manifest['protocol_sha256'], 'Proposal hash differs')
    destination = Path(args.output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    manifest_digest = sha(manifest_path)
    started = now()
    status_path = destination / 'run_status.json'
    try:
        for ordinal, job in enumerate(manifest['jobs']):
            output = destination / job['output_name']
            if output.exists():
                require(args.resume, 'Result already exists; preserve it or explicitly resume')
                existing = json.loads(output.read_text())
                require(existing.get('execution_manifest_sha256') == manifest_digest,
                        'Cannot resume records from a different execution manifest')
                require(existing.get('status') == 'ok', 'Resume refuses to silently skip a previous failure')
                continue
            spec_path = destination / ('spec-' + job['job_id'] + '.json')
            atomic_json(spec_path, job['spec'])
            launch = verify_hashes(manifest['source_sha256'])
            command = [sys.executable, str(Path(__file__).resolve()), 'worker',
                       '--kind', job['kind'], '--spec', str(spec_path),
                       '--manifest', str(manifest_path)]
            start = time.monotonic()
            try:
                child = subprocess.run(command, capture_output=True, text=True,
                                       timeout=args.worker_timeout,
                                       env={**os.environ, 'PYTHONHASHSEED': '0'})
                if child.returncode:
                    result = {'kind': job['kind'], 'status': 'worker_error', 'spec': job['spec'],
                              'returncode': child.returncode, 'stdout': child.stdout, 'stderr': child.stderr}
                else:
                    result = json.loads(child.stdout)
                    if child.stderr:
                        result['worker_stderr'] = child.stderr
            except subprocess.TimeoutExpired as error:
                result = {'kind': job['kind'], 'status': 'timeout', 'spec': job['spec'],
                          'timeout_seconds': args.worker_timeout, 'error': str(error),
                          'stdout': error.stdout.decode(errors='replace') if isinstance(error.stdout, bytes) else error.stdout,
                          'stderr': error.stderr.decode(errors='replace') if isinstance(error.stderr, bytes) else error.stderr}
            except Exception as error:
                result = {'kind': job['kind'], 'status': 'launcher_error', 'spec': job['spec'],
                          'error': repr(error), 'traceback': traceback.format_exc()}
            completion = source_hashes(manifest['source_sha256'])
            result.update(job_id=job['job_id'], execution_ordinal=ordinal,
                          execution_manifest_sha256=manifest_digest,
                          protocol_sha256=manifest['protocol_sha256'],
                          source_sha256_at_launch=launch, source_sha256_at_completion=completion,
                          runner_sha256=launch[str(Path(__file__).resolve().relative_to(ROOT))],
                          code_unchanged_during_worker=launch == completion,
                          launcher_argv=command, launcher_elapsed_seconds=time.monotonic()-start)
            if launch != completion:
                result['status'] = 'source_changed'
            atomic_json(output, result)
            atomic_json(status_path, {'started_utc': started, 'updated_utc': now(),
                                     'completed_job_ordinal': ordinal + 1, 'planned_jobs': len(manifest['jobs']),
                                     'last_job': job['job_id'], 'last_status': result['status'],
                                     'execution_manifest_sha256': manifest_digest})
            print(json.dumps({'completed': ordinal + 1, 'total': len(manifest['jobs']),
                              'job_id': job['job_id'], 'status': result['status']}), flush=True)
            require(result['status'] == 'ok', 'A job failed; sequence stopped with all evidence preserved')
        atomic_json(status_path, {'started_utc': started, 'finished_utc': now(),
                                 'planned_jobs': len(manifest['jobs']), 'all_jobs_completed': True,
                                 'execution_manifest_sha256': manifest_digest})
    finally:
        LOCK.unlink(missing_ok=True)


def selfcheck():
    """Small correctness-only execution; duration values are not research data."""
    warm_runtime()
    class Graph:
        adj = [[(1, 0), (2, 0), (3, 7)], [(2, 0), (3, 2)],
               [(1, 0), (3, 1), (4, 5)], [(4, 0)], []]
        n, m = 5, 10
    graph = Graph()
    expected = phase2.independent_oracle(graph, 0)
    advisor, _ = fit_linear(graph, 1, phase2.independent_oracle(graph, 1))
    checked = []
    for method in METHODS:
        for seed in (17, 29, 43):
            np.random.seed(seed)
            result = execute(graph, 0, method, advisor, checked=True)
            validation = validate_result(result, expected)
            checked.append({'method': method, 'seed': seed, 'validation': validation})
            del result
    timing_spec = {'case_id': 'tiny_selfcheck', 'train_source': 1, 'query_source': 0,
                   'queue_seed': 17, 'method_order': list(METHODS), 'repeat': 0}
    timing = _timing_loaded(timing_spec, graph, graph, expected,
                           phase2.independent_oracle(graph, 1))
    require(timing['status'] == 'ok', 'Tiny paired timing-path selfcheck failed')
    memory_checks = []
    for method in METHODS:
        for mode in ('enabled', 'disabled_during_query'):
            record = _graph_memory_loaded({'train_source': 1, 'query_source': 0,
                'queue_seed': 17, 'method': method, 'gc_mode': mode},
                graph, graph, expected, phase2.independent_oracle(graph, 1))
            require(record['distance_vector_verified'] and record['fully_drained'],
                    'Tiny graph-memory selfcheck failed')
            memory_checks.append({'method': method, 'mode': mode,
                                  'verified': True})
    # Exercise both bounded-live GC states with tiny traces; no timing benchmark.
    retention_checks = []
    for method in METHODS:
        for mode in ('enabled', 'disabled_during_trace'):
            record = retention_worker({'method': method, 'gc_mode': mode,
                'insertions': 160, 'max_live': 16, 'queue_seed': 101})
            require(record['all_extractions_correct'], 'Retention selfcheck failed')
            retention_checks.append({'method': method, 'mode': mode,
                                     'insertions': record['insertions'], 'extractions': record['extractions']})
    return {'status': 'passed', 'scope': 'Tiny correctness-only runner selfcheck; no research performance data',
            'graph_checks': checked, 'timing_path_method_checks': len(timing['records']),
            'graph_memory_checks': memory_checks, 'retention_checks': retention_checks,
            'runner_sha256': sha(Path(__file__))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('selfcheck')
    sub.add_parser('prepare')
    worker = sub.add_parser('worker')
    worker.add_argument('--kind', choices=('timing', 'graph_memory', 'retention'), required=True)
    worker.add_argument('--spec', required=True)
    worker.add_argument('--manifest', required=True)
    run = sub.add_parser('run')
    run.add_argument('--manifest', default=str(EXECUTION_MANIFEST))
    run.add_argument('--output-dir', default=str(RESULTS))
    run.add_argument('--approved-run', action='store_true')
    run.add_argument('--resume', action='store_true')
    run.add_argument('--worker-timeout', type=float, default=1800)
    args = parser.parse_args()
    if args.command == 'selfcheck':
        report = selfcheck()
        atomic_json(HERE / 'runner_selfcheck.json', report)
        print(json.dumps(report, indent=2))
    elif args.command == 'prepare':
        prepare_manifest()
    elif args.command == 'worker':
        print(json.dumps(worker_main(args.kind, args.spec, args.manifest)))
    else:
        run_manifest(args)


if __name__ == '__main__':
    main()
