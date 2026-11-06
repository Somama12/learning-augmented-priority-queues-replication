#!/usr/bin/env python3
"""Correctness and semantic preflight only: no clocks, timings or memory study."""
import hashlib
import heapq
import json
from pathlib import Path
import random

from ablation_queues import (OccurrenceBucket, METHODS, OCCURRENCE_METHODS,
                             make_ablation_queue)

HERE = Path(__file__).resolve().parent


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def bucket_preflight():
    checks = 0
    for seed in range(12):
        rng = random.Random(seed + 85913)
        bucket, reference = OccurrenceBucket(), []
        for _ in range(300):
            if not reference or rng.random() < .6:
                value = rng.randrange(24)
                bucket.append(value)
                reference.append(value)
            else:
                value = rng.choice(reference)
                bucket.remove(value)
                reference.remove(value)
            bucket.validate()
            require(list(bucket) == reference, 'bucket sequence mismatch')
            require(len(bucket) == len(reference), 'bucket length mismatch')
            if reference:
                require(bucket[-1] == reference[-1], 'last-hint mismatch')
            checks += 1
        while reference:
            value = reference[-1]
            bucket.remove(value)
            reference.remove(value)
            bucket.validate()
            require(list(bucket) == reference, 'bucket duplicate drain mismatch')
    return checks


def operations(seed, monotone):
    rng = random.Random(seed)
    pending, trace, last = [], [], 0
    for _ in range(180):
        if not pending or rng.random() < .6:
            value = (last if monotone else 0) + rng.randrange(24)
            heapq.heappush(pending, value)
            trace.append(('insert', value))
        else:
            last = heapq.heappop(pending)
            trace.append(('extract', last))
    while pending:
        trace.append(('extract', heapq.heappop(pending)))
    return trace


def checked_trace(method, trace, predictor, seed, count):
    queue = make_ablation_queue(method, rank_limit=10000, seed=seed, count=count)
    snapshots, operands, hints, draws = [], [], [], []
    sl = queue.sl
    sl.head._validation_id = 0
    compare, insert_es, sample_height = sl.compare, sl.insertES, sl.sampleHeight

    def traced_compare(a, b, dirty=False):
        operands.append((a, b, dirty))
        return compare(a, b, dirty)

    def traced_insert(source, value):
        hints.append((source._validation_id, source.value, source.height, value))
        node = insert_es(source, value)
        node._validation_id = len(hints)
        return node

    def traced_height():
        height = sample_height()
        draws.append(int(height))
        return height

    sl.compare, sl.insertES, sl.sampleHeight = traced_compare, traced_insert, traced_height
    previous_compares = 0
    for action, value in trace:
        extracted = None
        if action == 'insert':
            queue.insert(value, predictor(value))
        else:
            extracted = queue.extractMin()
            require(extracted == value, f'{method}: wrong extracted value')
        nodes, node = [], sl.findMin()
        while node is not None:
            nodes.append((node.value, int(node.height), node._validation_id))
            node = node.getNext()
        # These are diagnostic snapshots; never include this work in timings.
        buckets = tuple((rank, tuple(items)) for rank, items in sorted(queue.queue.rankVal.items()))
        value_ranks = tuple((key, tuple(ranks)) for key, ranks in sorted(queue.queue.valRank.items()))
        live_map = tuple((key, sl.nodes[key]._validation_id) for key in sorted(queue.queue.valRank))
        snapshots.append((extracted, nodes, buckets, value_ranks, live_map,
                          tuple(operands[previous_compares:])))
        previous_compares = len(operands)
        if count:
            require(sl.countComps >= 0, 'negative comparison count')
    require(queue.isEmpty(), 'undrained occurrence queue')
    if method.endswith(('map', 'detach')):
        require(not sl.nodes, 'cleanup retained historical node map')
    return (snapshots, hints, draws, getattr(sl, 'countComps', None)), queue.stats()


def queue_preflight():
    pairs, executions, grouped_checks = 0, 0, 0
    for seed in range(12):
        for monotone_inserts in (False, True):
            trace = operations(seed, monotone_inserts)
            for predictor in (lambda x: 0, lambda x: x // 4, lambda x: x):
                reference, _ = checked_trace('occ_list_retain', trace, predictor, seed + 101, True)
                for method in OCCURRENCE_METHODS:
                    counted, _ = checked_trace(method, trace, predictor, seed + 101, True)
                    plain, _ = checked_trace(method, trace, predictor, seed + 101, False)
                    require(counted == reference, f'{method}: counted live/operand/hint/height trace mismatch')
                    require(plain[:3] == reference[:3], f'{method}: stripping changed trace')
                    require(plain[3] is None, f'{method}: counter stripping failed')
                    pairs += 1
                    executions += 2
                # Grouping receives correctness checks, not a false trace-parity assertion.
                grouped = make_ablation_queue('grouped_compact_reference', 10000, seed + 101)
                for action, value in trace:
                    if action == 'insert':
                        grouped.insert(value, predictor(value))
                    else:
                        require(grouped.extractMin() == value, 'grouped reference wrong value')
                require(grouped.isEmpty(), 'grouped reference undrained')
                require(grouped.stats()['retained_node_map_entries'] == 0, 'grouped history retained')
                grouped_checks += 1
    return {'counted_parity_pairs': pairs, 'counted_and_counter_free_executions': executions,
            'grouped_correctness_traces': grouped_checks}


def independent_distances(adj, source):
    distance = [float('inf')] * len(adj)
    distance[source] = 0
    heap = [(0, source)]
    while heap:
        cost, vertex = heapq.heappop(heap)
        if cost != distance[vertex]:
            continue
        for target, weight in adj[vertex]:
            candidate = cost + weight
            if candidate < distance[target]:
                distance[target] = candidate
                heapq.heappush(heap, (candidate, target))
    return distance


def graph_preflight():
    cases = 0
    for seed in range(12):
        rng = random.Random(97213 + seed)
        n = 20
        adj = [[(rng.randrange(n), rng.randrange(8)) for _ in range(5)] for _ in range(n)]
        expected = independent_distances(adj, seed % n)
        for method in METHODS:
            queue = make_ablation_queue(method, 1000, seed + 501)
            distance = [float('inf')] * n
            distance[seed % n] = 0
            pending = {0: [seed % n]}
            queue.insert(0, 0)
            insertions, extractions = 1, 0
            while not queue.isEmpty():
                cost = queue.extractMin()
                vertex = pending[cost].pop()
                if not pending[cost]:
                    del pending[cost]
                extractions += 1
                if cost > distance[vertex]:
                    continue
                for target, weight in adj[vertex]:
                    candidate = cost + weight
                    if candidate < distance[target]:
                        distance[target] = candidate
                        pending.setdefault(candidate, []).append(target)
                        queue.insert(candidate, candidate // 4)
                        insertions += 1
            require(distance == expected, f'{method}: graph oracle mismatch')
            require(not pending and insertions == extractions, 'graph queue drain mismatch')
            cases += 1
    return cases


def main():
    report = {'purpose': 'Correctness/semantic preflight only; no performance measurements',
              'bucket_mutations': bucket_preflight(),
              **queue_preflight(),
              'graph_method_cases': graph_preflight(),
              'status': 'passed',
              'validity_contract': 'Finite numeric priorities, frozen deterministic monotone ranks; original arbitrary-advice duplicate failures are not claimed repaired.',
              'sources': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in (HERE / 'ablation_queues.py', HERE / 'preflight.py')}}
    (HERE / 'preflight_results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
