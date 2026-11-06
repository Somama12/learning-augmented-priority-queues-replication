"""Prepare and run the frozen native follow-up protocol, sequentially.

Writing this runner does not authorize concurrent timing with the Python study.
Default --stage plan only writes a small plan. Root explicitly schedules prepare
and run after Python timing/memory work. No export, compilation, oracle execution,
sanitizer test, or benchmark occurs merely by importing this module.

Native processes receive only graph data and selected source IDs, never heldout
distance arrays. Python validates their binary64 output against independently
certified reference distances after each process exits. Every failure is kept.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import shutil
import struct
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'native_benchmark.cpp'
WORKLOAD_MANIFEST = HERE / 'workload_manifest.json'
DEFAULT_DESTINATION = HERE / 'native_results'
SELECTED = ('brussels', 'ny', 'grid_8100', 'geometric_8192', 'sparse_8192',
            'sparse_2048__scale4', 'sparse_2048__lopsided')
METHODS = ('direct_heap', 'std_heap', 'std_multiset', 'grouped_heap', 'plain_skiplist',
           'guided_set_exact', 'guided_set_linear', 'guided_set_hist64',
           'guided_bitmap_exact', 'guided_bitmap_linear',
           'guided_bitmap_hist64', 'radix')
PROTOCOL_SEED = 693160271
REPETITIONS = 7
COMPILE_FLAGS = ('-O3', '-std=c++17', '-DNDEBUG')
SANITIZER_FLAGS = ('-O1', '-g', '-std=c++17',
                   '-fsanitize=address,undefined', '-fno-omit-frame-pointer')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def seed_for(*parts):
    return int.from_bytes(hashlib.sha256(':'.join(map(str, parts)).encode()).digest()[:4], 'big')


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def now():
    return datetime.now(timezone.utc).isoformat()


def make_plan():
    manifest = json.loads(WORKLOAD_MANIFEST.read_text())
    jobs = []
    for workload in SELECTED:
        metadata = manifest['workloads'][workload]
        for train_index, split in enumerate(metadata['splits']):
            for group in ('near', 'far'):
                query = next(q for q in split['queries'] if q['group'] == group)
                source = query['source']
                methods = [m for m in METHODS
                           if m != 'radix' or metadata['all_weights_integral_value']]
                key = f'{workload}_train{train_index}_{group}'
                paired_seed = seed_for(PROTOCOL_SEED, key)
                random.Random(paired_seed).shuffle(methods)
                jobs.append({'case_id': key, 'workload': workload,
                    'training_graph': metadata['training_graph'],
                    'train_source': split['train_source'], 'train_index': train_index,
                    'query_source': source, 'source_group': group,
                    'paired_seed': paired_seed, 'methods': methods,
                    'repetitions': REPETITIONS,
                    'expected_typed_distance_sha256': metadata['reference_distances'][str(source)]['sha256'],
                    'expected_reachable': metadata['reference_distances'][str(source)]['reachable'],
                    'target_adjacency_sha256': metadata['adjacency_sha256']})
    random.Random(PROTOCOL_SEED).shuffle(jobs)
    return {'protocol_version': 1, 'protocol_seed': PROTOCOL_SEED,
        'selected_workloads': list(SELECTED), 'repetitions': REPETITIONS,
        'source_design': 'three fitted training models per target, first heldout near and first heldout far source from each frozen split; no source reselection',
        'shift_design': 'fit on training_graph (unshifted base), query target workload with frozen predictor',
        'job_order': 'one native process per graph/train/query case; seeded shuffle of all 42 cases',
        'method_order': 'driver supplies a seeded shuffled list; native program must reshuffle every repetition and record order, with paired random-height seeds',
        'applicability': 'radix included only when every target weight has integral value; scalar-double distances must remain exactly representable for integer workloads',
        'scope': 'native CPU engineering control; no new predecessor or radix algorithm claim; synthetic shifts are controlled stresses',
        'baseline_semantics': 'lazy insertion and stale-entry Dijkstra semantics; direct_heap stores distance/reverse-insertion-sequence/vertex without a pending map for deployment comparison; std_heap shares the scalar-key/pending-map kernel for ablations; both preserve equal-distance LIFO order. Decrease-key benchmark results are not directly interchangeable without changing the update protocol.',
        'prior_art': [
            {'title': 'Faster Algorithms for the Shortest Path Problem',
             'authors': 'Ahuja, Mehlhorn, Orlin, Tarjan', 'year': 1990,
             'url': 'https://www.princeton.edu/~alaink/Orf467F11/AhujaRadixHeap.pdf',
             'relevance': 'established monotone integer radix-heap baseline; original decrease-key bound is not asserted for lazy reinsertion'},
            {'title': 'Engineering Predecessor Data Structures for Dynamic Integer Sets',
             'authors': 'Dinklage, Fischer, Herlez', 'year': 2021,
             'url': 'https://arxiv.org/html/2104.06740v1',
             'relevance': 'established integer predecessor and bitmap-bucket engineering; not a novelty claim for our bitmap index'}],
        'oracle_policy': 'independent Python heapq reference plus parent/triangle certificate; native pair-heap oracle; compare little-endian IEEE754 binary64 distance arrays exactly outside timing',
        'compile_flags': list(COMPILE_FLAGS), 'sanitizer_flags': list(SANITIZER_FLAGS),
        'no_fast_math': True,
        'workload_manifest_sha256': sha256(WORKLOAD_MANIFEST),
        'workload_generator_sha256': manifest['generator_sha256'],
        'runner_sha256': sha256(__file__), 'jobs': jobs}


def export_graph(graph, path):
    """Text round-trips every int/double weight and preserves every arc/order."""
    path = Path(path)
    with path.open('w', encoding='ascii', newline='\n') as stream:
        stream.write(f'{graph.n} {graph.m}\n')
        for u, row in enumerate(graph.adj):
            for v, weight in row:
                stream.write(f'{u} {v} {repr(weight)}\n')


def binary64_distances(distances, path):
    h = hashlib.sha256()
    with Path(path).open('wb') as stream:
        for value in distances:
            encoded = struct.pack('<d', float(value))
            stream.write(encoded)
            h.update(encoded)
    return h.hexdigest()


def prepare_inputs(plan, destination):
    # Imports and graph construction are deferred until the explicit prepare step.
    from workloads import load, graph_hash, reference_distances, _distance_hash
    manifest = json.loads(WORKLOAD_MANIFEST.read_text())
    if sha256(WORKLOAD_MANIFEST) != plan['workload_manifest_sha256']:
        raise RuntimeError('workload manifest changed since native plan')
    if sha256(HERE / 'workloads.py') != plan['workload_generator_sha256']:
        raise RuntimeError('workload generator changed since input freeze')
    inputs = destination / 'inputs'
    oracle_dir = destination / 'oracles'
    inputs.mkdir(parents=True, exist_ok=True)
    oracle_dir.mkdir(parents=True, exist_ok=True)
    names = list(dict.fromkeys(list(SELECTED) + [j['training_graph'] for j in plan['jobs']]))
    exported = {}
    for name in names:
        graph = load(name)
        metadata = manifest['workloads'][name]
        adjacency_hash = graph_hash(graph)
        if adjacency_hash != metadata['adjacency_sha256']:
            raise RuntimeError(f'generated graph does not match frozen hash: {name}')
        if metadata['all_weights_integral_value'] and metadata['n_times_max_weight_bound'] >= 2**53:
            raise RuntimeError(f'integer distance bound exceeds exact binary64 representation: {name}')
        path = inputs / (name + '.txt')
        export_graph(graph, path)
        exported[name] = {'path': str(path.resolve()), 'text_sha256': sha256(path),
            'adjacency_sha256': adjacency_hash, 'n': graph.n, 'm': graph.m,
            'min_weight': metadata['min_weight'], 'max_weight': metadata['max_weight'],
            'integer_values': metadata['all_weights_integral_value'],
            'n_times_max_weight_bound': metadata['n_times_max_weight_bound'],
            'raw_input_sha256': metadata.get('source_sha256'),
            'target_transform': metadata['transform']}
        for job in plan['jobs']:
            if job['workload'] != name:
                continue
            distances = reference_distances(graph, job['query_source'])
            actual_typed_hash = _distance_hash(distances)
            if actual_typed_hash != job['expected_typed_distance_sha256']:
                raise RuntimeError(f'independent oracle changed: {job["case_id"]}')
            oracle_path = oracle_dir / (job['case_id'] + '.f64le')
            job['oracle_binary64_sha256'] = binary64_distances(distances, oracle_path)
            job['oracle_binary64_path'] = str(oracle_path.resolve())
            job['oracle_binary64_bytes'] = 8 * graph.n
            del distances
        del graph
        gc.collect()
    plan['exported_inputs'] = exported
    plan['inputs_prepared_at_utc'] = now()
    return plan


def command_logged(argv, directory, tag, timeout):
    directory.mkdir(parents=True, exist_ok=True)
    stdout_path = directory / f'{tag}.stdout.txt'
    stderr_path = directory / f'{tag}.stderr.txt'
    started = now()
    wall_start = time.perf_counter()
    with stdout_path.open('w') as stdout, stderr_path.open('w') as stderr:
        try:
            process = subprocess.run(argv, cwd=HERE, stdout=stdout, stderr=stderr,
                                     check=False, timeout=timeout)
            return_code = process.returncode
            exception = None
        except (subprocess.TimeoutExpired, OSError) as error:
            return_code = None
            exception = repr(error)
    return {'argv': list(map(str, argv)), 'cwd': str(HERE),
            'returncode': return_code, 'exception': exception,
            'started_at_utc': started, 'finished_at_utc': now(),
            'process_lifecycle_wall_seconds': time.perf_counter()-wall_start,
            'lifecycle_scope': 'whole subprocess including IO; not algorithm-only runtime',
            'stdout_path': str(stdout_path), 'stderr_path': str(stderr_path)}


def compile_program(destination, compiler, sanitizers=False):
    compiler_path = shutil.which(compiler)
    if compiler_path is None:
        raise RuntimeError(f'compiler unavailable: {compiler}')
    build = destination / 'build'
    build.mkdir(parents=True, exist_ok=True)
    binary = build / ('native_benchmark_sanitized' if sanitizers else 'native_benchmark')
    flags = SANITIZER_FLAGS if sanitizers else COMPILE_FLAGS
    argv = [compiler_path, *flags, str(SOURCE), '-o', str(binary)]
    result = command_logged(argv, build, 'compile_sanitized' if sanitizers else 'compile', 180)
    version = subprocess.run([compiler_path, '--version'], capture_output=True, text=True, check=False)
    result.update({'compiler': compiler_path, 'compiler_version': version.stdout,
                   'compile_flags': list(flags), 'source_sha256': sha256(SOURCE),
                   'binary_path': str(binary.resolve()), 'sanitizers': sanitizers})
    if result['returncode'] == 0 and binary.exists():
        result['binary_sha256'] = sha256(binary)
    atomic_json(build / ('sanitizer_build.json' if sanitizers else 'build.json'), result)
    if result['returncode'] != 0:
        raise RuntimeError('native compilation failed; logs preserved in ' + str(build))
    return result


def run_selftest(build_metadata, destination, sanitizers=False):
    tag = 'sanitizer_selftest' if sanitizers else 'selftest'
    result = command_logged([build_metadata['binary_path'], '--selftest'], destination/'build', tag, 180)
    result['binary_sha256'] = build_metadata['binary_sha256']
    atomic_json(destination/'build'/(tag+'.json'), result)
    if result['returncode'] != 0:
        raise RuntimeError(f'{tag} failed; output preserved')
    return result


def runtime_metadata():
    return {'python': sys.version, 'platform': platform.platform(),
            'machine': platform.machine(), 'processor': platform.processor(),
            'logical_cpu_count': os.cpu_count(),
            'timing_owner': 'root-coordinated sequential native follow-up after Python timing/memory',
            'thread_env': {key: os.environ.get(key) for key in
                           ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS')},
            'cpu_affinity': 'not pinned by this runner',
            'process_model': 'one subprocess per graph/train/query case, no concurrent workers'}


def verify_distances(path, job):
    if not path.exists():
        return {'passed': False, 'reason': 'native distance file missing'}
    actual_size = path.stat().st_size
    actual_hash = sha256(path)
    passed = (actual_size == job['oracle_binary64_bytes'] and
              actual_hash == job['oracle_binary64_sha256'])
    output = {'passed': passed, 'expected_bytes': job['oracle_binary64_bytes'],
        'actual_bytes': actual_size, 'expected_sha256': job['oracle_binary64_sha256'],
        'actual_sha256': actual_hash}
    if not passed and actual_size == job['oracle_binary64_bytes']:
        expected = Path(job['oracle_binary64_path']).read_bytes()
        actual = path.read_bytes()
        for vertex in range(actual_size//8):
            before = expected[8*vertex:8*vertex+8]
            after = actual[8*vertex:8*vertex+8]
            if before != after:
                output['first_mismatch'] = {'vertex': vertex,
                    'expected_hex': before.hex(), 'actual_hex': after.hex(),
                    'expected': repr(struct.unpack('<d', before)[0]),
                    'actual': repr(struct.unpack('<d', after)[0])}
                break
    return output


def validate_native_records(metadata, rows, job):
    """Require complete paired coverage and explicit native success signals."""
    errors = []
    for flag in ('all_exact_distances_passed', 'all_queues_drained', 'common_lifo_work_counts_passed'):
        if metadata.get(flag) is not True:
            errors.append('native metadata flag not true: ' + flag)
    for field, expected in (('source', job['query_source']), ('train', job['train_source']),
                            ('repeat', job['repetitions']), ('seed', job['paired_seed'])):
        if metadata.get(field) != expected:
            errors.append('native metadata differs from job: ' + field)
    if Counter(metadata.get('methods', [])) != Counter(job['methods']):
        errors.append('native method list differs from requested methods')
    expected_count = len(job['methods']) * job['repetitions']
    if metadata.get('timed_records') != expected_count or len(rows) != expected_count:
        errors.append('native timing record count differs from protocol')
    coverage = Counter()
    seeds, orders = defaultdict(set), defaultdict(list)
    try:
        for row in rows:
            repetition = int(row['repetition'])
            coverage[(row['method'], repetition)] += 1
            seeds[repetition].add(int(row['height_seed']))
            orders[repetition].append(int(row['order']))
            if int(row['source']) != job['query_source'] or int(row['train']) != job['train_source']:
                errors.append('CSV source identity differs from job')
            if row['correct'] != 'true' or row['drained'] != 'true':
                errors.append('CSV correctness/drain check failed')
            if row['distance_fnv1a64'] != str(metadata.get('distance_fnv1a64')):
                errors.append('CSV distance hash differs from native oracle hash')
            wall, cpu = float(row['wall_seconds']), float(row['cpu_seconds'])
            if not (math.isfinite(wall) and math.isfinite(cpu)) or wall <= 0 or cpu < 0:
                errors.append('invalid native timing value')
        expected_coverage = Counter((method, repetition) for method in job['methods']
                                    for repetition in range(job['repetitions']))
        if coverage != expected_coverage:
            errors.append('method/repetition coverage is not exactly paired')
        for repetition in range(job['repetitions']):
            if len(seeds[repetition]) != 1:
                errors.append('height seeds differ within paired repetition')
            if sorted(orders[repetition]) != list(range(len(job['methods']))):
                errors.append('method execution order is not a permutation')
    except (KeyError, TypeError, ValueError) as error:
        errors.append('invalid native CSV schema/value: ' + repr(error))
    return list(dict.fromkeys(errors))


def run_jobs(plan, build, destination, timeout):
    if sha256(SOURCE) != build['source_sha256']:
        raise RuntimeError('native source changed after compilation')
    if sha256(build['binary_path']) != build['binary_sha256']:
        raise RuntimeError('native binary changed after compilation')
    results = {'protocol': plan, 'build': build, 'runtime': runtime_metadata(),
               'started_at_utc': now(), 'cases': [], 'failures': []}
    output_path = destination / 'native_experiment.json'
    if output_path.exists():
        raise RuntimeError('native results already exist; choose a fresh --destination to preserve all previous records')
    if sha256(WORKLOAD_MANIFEST) != plan['workload_manifest_sha256']:
        raise RuntimeError('frozen workload manifest changed before native run')
    for name, exported in plan['exported_inputs'].items():
        if sha256(exported['path']) != exported['text_sha256']:
            raise RuntimeError('exported graph changed before native run: ' + name)
    for ordinal, job in enumerate(plan['jobs']):
        folder = destination / 'cases' / job['case_id']
        folder.mkdir(parents=True, exist_ok=True)
        prefix = folder / 'native'
        distance_path = folder / 'distances.f64le'
        argv = [build['binary_path'], '--graph', plan['exported_inputs'][job['workload']]['path'],
            '--train-graph', plan['exported_inputs'][job['training_graph']]['path'],
            '--train', str(job['train_source']), '--source', str(job['query_source']),
            '--repeat', str(job['repetitions']), '--seed', str(job['paired_seed']),
            '--methods', ','.join(job['methods']), '--output', str(prefix),
            '--distances-output', str(distance_path)]
        record = {'case': job, 'execution_ordinal': ordinal,
                  'process': command_logged(argv, folder, 'process', timeout)}
        errors = []
        if record['process']['returncode'] != 0:
            errors.append('nonzero return code or process exception')
        native_json, native_csv = prefix.with_suffix('.json'), prefix.with_suffix('.csv')
        rows = []
        if native_json.exists():
            try:
                record['native_metadata'] = json.loads(native_json.read_text())
            except (ValueError, OSError) as error:
                errors.append('unreadable native metadata: ' + repr(error))
        else:
            errors.append('native JSON missing')
        if native_csv.exists():
            with native_csv.open(newline='') as stream:
                rows = list(csv.DictReader(stream))
            record['timing_csv'] = str(native_csv)
            record['timing_csv_sha256'] = sha256(native_csv)
            record['timing_rows'] = len(rows)
            if not rows:
                errors.append('native timing CSV has no records')
        else:
            errors.append('native CSV missing')
        if 'native_metadata' in record:
            errors.extend(validate_native_records(record['native_metadata'], rows, job))
        record['distance_verification'] = verify_distances(distance_path, job)
        if not record['distance_verification']['passed']:
            errors.append('independent distance verification failed')
        record['passed'] = not errors
        record['errors'] = errors
        atomic_json(folder/'case_record.json', record)
        results['cases'].append(record)
        if errors:
            results['failures'].append({'case_id': job['case_id'], 'errors': errors})
        atomic_json(output_path, results)
        print(json.dumps({'completed': ordinal+1, 'total': len(plan['jobs']),
                          'case_id': job['case_id'], 'passed': record['passed']}), flush=True)
    results['finished_at_utc'] = now()
    results['all_passed'] = not results['failures']
    atomic_json(output_path, results)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('plan', 'prepare', 'run', 'all'), default='plan')
    parser.add_argument('--destination', type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument('--compiler', default='c++')
    parser.add_argument('--sanitizers', action='store_true',
                        help='compile and run separate ASAN+UBSAN selftest before native timing')
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if (destination/'native_experiment.json').exists():
        raise RuntimeError('native results already exist; use a fresh --destination to preserve the prior protocol and raw results')
    plan_path = destination/'native_protocol.json'
    if args.stage in ('plan', 'prepare', 'all'):
        plan = make_plan()
        atomic_json(plan_path, plan)
    else:
        plan = json.loads(plan_path.read_text())
    if args.stage == 'plan':
        print(json.dumps({'planned_cases': len(plan['jobs']), 'no_exports_compilation_or_timings': True,
                          'plan': str(plan_path)}))
        return
    if args.stage in ('prepare', 'all'):
        plan = prepare_inputs(plan, destination)
        atomic_json(plan_path, plan)
        build = compile_program(destination, args.compiler)
        run_selftest(build, destination)
        if args.sanitizers:
            sanitized = compile_program(destination, args.compiler, sanitizers=True)
            run_selftest(sanitized, destination, sanitizers=True)
    else:
        build = json.loads((destination/'build'/'build.json').read_text())
        if args.sanitizers:
            sanitized = compile_program(destination, args.compiler, sanitizers=True)
            run_selftest(sanitized, destination, sanitizers=True)
    if args.stage in ('run', 'all'):
        result = run_jobs(plan, build, destination, args.timeout)
        if not result['all_passed']:
            raise SystemExit('Some native cases failed; every case and failure is preserved.')
    else:
        print(json.dumps({'prepared_cases': len(plan['jobs']), 'timings_started': False,
                          'plan': str(plan_path)}))


if __name__ == '__main__':
    main()
