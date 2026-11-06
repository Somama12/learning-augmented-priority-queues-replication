"""Synthetic predecessor-index microbenchmark, not an end-to-end PQ claim.

Default execution only performs correctness preflight. --run-timings explicitly
enables five paired repetitions after preflight. Root coordinates any timed run.
Traces are generated once per case and reused byte-for-byte by every method.
Initial construction is measured separately from mixed operations. No stats,
oracle, counters or assertions occur inside timed trace replay. Optional memory
measurements are separate tracemalloc runs and are not RSS measurements.
"""
from __future__ import annotations
import argparse
import bisect
import gc
import hashlib
import json
from pathlib import Path
import platform
import random
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'deps'))
from sortedcontainers import SortedList
from indexes import BitmapPredecessorSet
from veb_index import SparseVEBSet


class SortedListIndex:
    """Index adapter for traces whose inserted keys are guaranteed absent.

    This matches OnlineSL's external rankVal membership guard. Avoid adding an
    extra SortedList membership search absent from the measured queue artifact.
    """
    def __init__(self, universe):
        self.universe = universe
        self.values = SortedList()
    def add(self, key):
        self.values.add(key)
        return True
    def remove(self, key):
        self.values.remove(key)
    def predecessor_strict(self, key):
        position = self.values.bisect_left(key)
        return self.values[position-1] if position else None
    def __len__(self):
        return len(self.values)
    def stats(self):
        return {'kind': 'sortedlist_known_new_index', 'size': len(self),
                'universe': self.universe}


FACTORIES = {'sortedlist': SortedListIndex,
             'bitmap': BitmapPredecessorSet,
             'veb': SparseVEBSet}


def make_cases(operations=10000, seed=193771):
    if operations < 4 or operations % 4:
        raise ValueError('operations must be a positive multiple of four')
    cases, skipped = [], []
    for universe in (256, 4096, 300001):
        for active in (16, 128, 512):
            for distribution in ('uniform', 'clustered'):
                name = f'U{universe}_n{active}_{distribution}'
                if active >= universe:
                    skipped.append({'name': name,
                        'reason': 'active cardinality must be smaller than universe; no silent clamping'})
                    continue
                case_seed = int.from_bytes(hashlib.sha256(f'{seed}:{name}'.encode()).digest()[:8], 'big')
                rng = random.Random(case_seed)
                width = universe if distribution == 'uniform' else min(universe, max(active+16, (universe+127)//128))
                low = rng.randrange(universe-width+1)
                slots = rng.sample(range(low, low+width), active)
                initial = slots.copy()
                present = set(slots)
                trace = []
                for _ in range(operations//4):
                    slot = rng.randrange(active)
                    old = slots[slot]
                    # Choose while old is still present so replacement differs.
                    new = rng.randrange(low, low+width)
                    while new in present:
                        new = rng.randrange(low, low+width)
                    present.remove(old)
                    present.add(new)
                    slots[slot] = new
                    trace.extend(((1, old), (0, new)))
                    for _ in range(2):
                        if distribution == 'clustered' and rng.random() < .9:
                            query = rng.randrange(low, low+width+1)
                        else:
                            query = rng.randrange(-1, universe+2)
                        trace.append((2, query))
                digest = hashlib.sha256(json.dumps([initial, trace], separators=(',', ':')).encode()).hexdigest()
                cases.append({'name': name, 'universe': universe, 'active': active,
                    'distribution': distribution, 'seed': case_seed,
                    'cluster_interval': [low, low+width], 'operations': operations,
                    'mix': {'add': operations//4, 'remove': operations//4,
                            'strict_predecessor': operations//2},
                    'trace_sha256': digest, 'initial': initial, 'trace': trace})
    return cases, skipped


def public_case(case):
    return {k: v for k, v in case.items() if k not in ('initial', 'trace')}


def construct(factory, case):
    index = factory(case['universe'])
    for value in case['initial']:
        index.add(value)
    return index


def replay(index, trace):
    add, remove, predecessor = index.add, index.remove, index.predecessor_strict
    last_answer = None
    for operation, value in trace:
        if operation == 0:
            add(value)
        elif operation == 1:
            remove(value)
        else:
            last_answer = predecessor(value)
    return last_answer


def preflight(cases):
    results = []
    for case in cases:
        indexes = {name: construct(factory, case) for name, factory in FACTORIES.items()}
        oracle = sorted(case['initial'])
        for operation, value in case['trace']:
            if operation == 0:
                position = bisect.bisect_left(oracle, value)
                assert position == len(oracle) or oracle[position] != value
                oracle.insert(position, value)
                for index in indexes.values():
                    assert index.add(value) is True
            elif operation == 1:
                position = bisect.bisect_left(oracle, value)
                assert oracle[position] == value
                oracle.pop(position)
                for index in indexes.values():
                    index.remove(value)
            else:
                position = bisect.bisect_left(oracle, value)
                expected = oracle[position-1] if position else None
                for name, index in indexes.items():
                    actual = index.predecessor_strict(value)
                    assert actual == expected, (case['name'], name, value, expected, actual)
            assert all(len(index) == len(oracle) for index in indexes.values())
        for name, index in indexes.items():
            assert index.predecessor_strict(-1) is None
            assert index.predecessor_strict(case['universe']+1) == oracle[-1]
            if name != 'sortedlist':
                assert index.add(oracle[0]) is False
        results.append({'case': case['name'], 'passed': True,
                        'operations_checked_per_method': len(case['trace']),
                        'methods': list(indexes)})
    return results


def timed_runs(cases, repetitions=5, seed=702113):
    rng = random.Random(seed)
    records = []
    for case in cases:
        # Untimed warm-up for each implementation on this exact trace.
        for factory in FACTORIES.values():
            index = construct(factory, case)
            replay(index, case['trace'])
            del index
        for repetition in range(repetitions):
            order = list(FACTORIES)
            rng.shuffle(order)
            for name in order:
                gc.collect()
                wall = time.perf_counter_ns()
                cpu = time.process_time_ns()
                index = construct(FACTORIES[name], case)
                construction_cpu = time.process_time_ns()-cpu
                construction_wall = time.perf_counter_ns()-wall
                wall = time.perf_counter_ns()
                cpu = time.process_time_ns()
                replay(index, case['trace'])
                operation_cpu = time.process_time_ns()-cpu
                operation_wall = time.perf_counter_ns()-wall
                records.append({'case': case['name'], 'method': name,
                    'repetition': repetition, 'execution_order': order,
                    'construction_cpu_ns': construction_cpu,
                    'construction_wall_ns': construction_wall,
                    'operations_cpu_ns': operation_cpu,
                    'operations_wall_ns': operation_wall,
                    'construction_plus_operations_cpu_ns': construction_cpu+operation_cpu,
                    'construction_plus_operations_wall_ns': construction_wall+operation_wall,
                    'stats_after': index.stats()})
                del index
    return records


def memory_runs(cases):
    import tracemalloc
    records = []
    for case in cases:
        for name, factory in FACTORIES.items():
            gc.collect()
            tracemalloc.start()
            index = construct(factory, case)
            construction_current, construction_peak = tracemalloc.get_traced_memory()
            replay(index, case['trace'])
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            records.append({'case': case['name'], 'method': name,
                'construction_current_bytes': construction_current,
                'construction_peak_bytes': construction_peak,
                'after_current_bytes': current, 'overall_peak_bytes': peak,
                'scope': 'Python allocations after already generated trace; tracemalloc, not RSS'})
            del index
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-timings', action='store_true')
    parser.add_argument('--memory', action='store_true')
    parser.add_argument('--operations', type=int, default=10000)
    parser.add_argument('--repetitions', type=int, default=5)
    parser.add_argument('--output', type=Path, default=HERE/'index_microbench_preflight.json')
    args = parser.parse_args()
    cases, skipped = make_cases(args.operations)
    checked = preflight(cases)
    result = {'kind': 'synthetic dynamic-predecessor microbenchmark; no end-to-end claim',
        'timing_enabled': args.run_timings, 'python': sys.version,
        'platform': platform.platform(), 'cases': list(map(public_case, cases)),
        'skipped': skipped, 'preflight': checked,
        'methods': list(FACTORIES), 'repetitions': args.repetitions,
        'semantic_contract': 'bounded active keys with strict predecessor; inserted keys guaranteed absent and removed keys guaranteed present; SortedList has no extra membership search, matching external rankVal guard',
        'timing_notes': 'unmodified GC policy; collect before each run; one untimed warmup per method/case; shuffled method order per repetition; stats outside clock interval',
        'source_sha256': {name: hashlib.sha256((HERE/name).read_bytes()).hexdigest()
                          for name in ('index_microbench.py','indexes.py','veb_index.py')}}
    if args.run_timings:
        result['timings'] = timed_runs(cases, args.repetitions)
    if args.memory:
        result['memory'] = memory_runs(cases)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'cases': len(cases), 'preflight_passed': True,
                      'timing_enabled': args.run_timings, 'output': str(args.output)}))


if __name__ == '__main__':
    main()
