#!/usr/bin/env python3
"""Controlled test of calibration versus collision structure in OnlineSL.

Distinct keys and a fixed operation stream isolate the mechanism. Two strictly
increasing relabelings of identical buckets give different global rank errors,
but must have identical queue states and counted clean comparisons. Both label
maps remain in [1,N]. No wall-clock or algorithmic novelty claim is made.
"""
import ast
import bisect
from collections import Counter
import hashlib
import heapq
import json
from pathlib import Path
import random
import statistics
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'deps'))
import numpy as np
from sortedcontainers import SortedList

source = HERE / 'upstream' / 'skiplist.py'
tree = ast.parse(source.read_text())
names = {'Node', 'damagedCompare', 'SkipList', 'OnlineSL'}
ns = {'np': np, 'SortedList': SortedList}
exec(compile(ast.Module(body=[n for n in tree.body
    if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names],
    type_ignores=[]), str(source), 'exec'), ns)


def make_stream(n, seed):
    rng = random.Random(seed)
    keys = list(range(1, n+1))
    rng.shuffle(keys)
    stream, active = [], 0
    for key in keys:
        stream.append(('insert', key))
        active += 1
        if rng.random() < 0.45:
            stream.append(('extract', None))
            active -= 1
    stream.extend([('extract', None)] * active)
    return stream


def label(key, n, bins, shape):
    bucket = (key-1)*bins//n
    if shape == 'center':
        return min(n, 1 + (2*bucket+1)*n//(2*bins))
    if shape == 'cubic':
        return 1 + bucket + ((n-bins)*bucket**3)//((bins-1)**3)
    raise ValueError(shape)


def run(stream, n, bins, shape, seed):
    np.random.seed(seed)
    q = ns['OnlineSL']()
    comparison_trace = hashlib.sha256()
    original_compare = q.sl.cleanCompare
    def record_compare(a, b):
        comparison_trace.update(f'{a}:{b};'.encode())
        return original_compare(a, b)
    q.sl.cleanCompare = record_compare
    reference, active, events, errors, gaps, bounds = [], [], [], [], [], []
    bucket_counts = Counter()
    peak_bucket = 0
    for op, value in stream:
        if op == 'insert':
            rank = label(value, n, bins, shape)
            errors.append(abs(rank-value))
            # Read-only diagnostics before insertion; not counted as PQ work.
            previous_rank = q.getPredecessor(q.veb, rank)
            previous_value = q.rankVal[previous_rank][-1]
            predecessor_position = bisect.bisect_left(active, value)-1
            if previous_value == -np.inf:
                hint_position = -1
                bound = bucket_counts[rank]
            else:
                assert previous_value < value
                hint_position = bisect.bisect_left(active, previous_value)
                assert active[hint_position] == previous_value
                bound = bucket_counts[previous_rank]-1+bucket_counts[rank]
            gap = predecessor_position-hint_position
            assert 0 <= gap <= bound, (value, previous_value, gap, bound)
            gaps.append(gap)
            bounds.append(bound)
            q.insert(value, rank)
            heapq.heappush(reference, value)
            bisect.insort(active, value)
            bucket_counts[rank] += 1
            peak_bucket = max(peak_bucket, bucket_counts[rank])
            events.append((op, q.countComps))
        else:
            expected = heapq.heappop(reference)
            observed = q.extractMin()
            assert observed == expected
            assert active.pop(0) == expected
            bucket_counts[label(expected, n, bins, shape)] -= 1
            events.append((op, q.countComps, observed))
        assert q.isEmpty() == (not reference)
    return {
        'n': n, 'bins': bins, 'shape': shape, 'seed': seed,
        'clean_comparisons': int(q.countComps),
        'global_rank_mae': statistics.mean(errors),
        'global_rank_max_error': max(errors),
        'max_active_bucket_size': peak_bucket,
        'max_hint_rank_gap': max(gaps),
        'mean_hint_rank_gap': statistics.mean(gaps),
        'max_two_bucket_bound': max(bounds),
        'all_insertions_satisfy_two_bucket_bound': True,
        'all_extractions_match_heapq': True,
        'per_operation_digest': hashlib.sha256(json.dumps(events).encode()).hexdigest(),
        'comparison_operand_trace_digest': comparison_trace.hexdigest(),
    }


def main():
    n = 2048
    records, pairs = [], []
    for seed in range(10):
        stream = make_stream(n, seed)
        for bins in [8, 32, 128, 512, 2048]:
            a = run(stream, n, bins, 'center', seed)
            b = run(stream, n, bins, 'cubic', seed)
            assert a['per_operation_digest'] == b['per_operation_digest']
            assert a['comparison_operand_trace_digest'] == b['comparison_operand_trace_digest']
            records.extend([a, b])
            pairs.append({'seed': seed, 'bins': bins,
                          'identical_per_operation_counts_and_extractions': True,
                          'identical_clean_comparison_operand_trace': True,
                          'identical_map_negative_control': bins == n})
    output = {
        'upstream_commit': '3915cc261e87949b2b0aa724673c2227492d1b08',
        'upstream_skiplist_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'design': '2048 distinct keys; random insertion permutation; extract with probability .45 after insertion; final drain; fixed heights across paired runs',
        'interpretation': 'Calibration changes while order and collision groups are fixed. This verifies an explanatory corollary, not novelty or runtime superiority.',
        'records': records, 'paired_checks': pairs,
        'bound_checks': n*len(records),
    }
    (HERE/'mechanism_results.json').write_text(json.dumps(output, indent=2)+'\n')
    print(json.dumps({'runs':len(records), 'paired_checks':len(pairs),
                      'bound_checks':output['bound_checks'],
                      'sample_center':records[2], 'sample_cubic':records[3]}, indent=2))


if __name__ == '__main__':
    main()
