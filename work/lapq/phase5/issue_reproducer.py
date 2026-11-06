#!/usr/bin/env python3
"""Correctness-only reproducer for historical OnlineSL node-map retention.

From a checkout of the pinned upstream repository:
  python issue_reproducer.py --upstream skiplist.py
Requires the upstream imports: numpy, matplotlib and sortedcontainers.
No upstream file is edited; the cleanup is an independent subclass.
"""
import argparse
import hashlib
import heapq
import importlib.util
import json
from pathlib import Path
import random
import sys

COMMIT = '3915cc261e87949b2b0aa724673c2227492d1b08'
SOURCE_SHA256 = '63282bd3c84e89684380ab690c48f43a24fa013f4323a57e045fc94fc96b4a3c'


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, default=Path('skiplist.py'))
    parser.add_argument('--dependency-dir', type=Path, help='Optional existing dependency directory; installs nothing')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    source_before = hashlib.sha256(args.upstream.read_bytes()).hexdigest()
    check(source_before == SOURCE_SHA256, 'Unexpected upstream skiplist.py revision')
    if args.dependency_dir:
        sys.path.insert(0, str(args.dependency_dir.resolve()))
    spec = importlib.util.spec_from_file_location('unchanged_upstream_skiplist', args.upstream)
    upstream = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upstream)

    class MapCleanup(upstream.OnlineSL):
        def extractMin(self):
            value = super().extractMin()
            # Delete only after the final live occurrence has been removed.
            # Do not detach any extracted node's next/prev pointers.
            if value not in self.valRank:
                del self.sl.nodes[value]
            return value

    pointer_checks = 0

    def pop(queue, oracle):
        nonlocal pointer_checks
        node = queue.sl.findMin()
        old_next, old_prev = list(node.next), list(node.prev)
        actual, expected = queue.extractMin(), heapq.heappop(oracle)
        check(actual == expected, 'Extraction differs from heapq')
        check(node.next == old_next and node.prev == old_prev, 'Extracted node pointers were changed')
        pointer_checks += 1
        if isinstance(queue, MapCleanup):
            check(set(queue.sl.nodes) == set(oracle), 'Cleanup node-map keys do not match live values')
        return actual

    unique = []
    for cls in (upstream.OnlineSL, MapCleanup):
        upstream.np.random.seed(101)
        queue, oracle, peak = cls(), [], 0
        for value in range(1000):
            if len(oracle) == 128:
                pop(queue, oracle)
            queue.insert(value, 0)
            heapq.heappush(oracle, value)
            peak = max(peak, len(oracle))
        while oracle:
            pop(queue, oracle)
        check(queue.isEmpty() and queue.sl.head.getNext() is None and not queue.valRank, 'Queue did not drain')
        retained = len(queue.sl.nodes)
        check(retained == (1000 if cls is upstream.OnlineSL else 0), 'Unexpected retained-map cardinality')
        unique.append({'variant': cls.__name__, 'insertions': 1000, 'extractions': 1000,
                       'maximum_live_entries': peak, 'drained': True,
                       'node_map_entries_after_drain': retained})

    duplicate_controls = []
    for cls in (upstream.OnlineSL, MapCleanup):
        upstream.np.random.seed(101)
        queue, oracle = cls(), []
        for value in (1, 1):
            queue.insert(value, value)
            heapq.heappush(oracle, value)
        representative = queue.sl.nodes[1]
        check(queue.sl.findMin() is representative, 'Control did not exercise extracted duplicate representative')
        pop(queue, oracle)
        check(queue.sl.nodes[1] is representative, 'Duplicate hint representative was removed too early')
        queue.insert(2, 2)
        heapq.heappush(oracle, 2)
        output = [pop(queue, oracle), pop(queue, oracle)]
        check(output == [1, 2] and queue.isEmpty(), 'Duplicate continuation failed')
        duplicate_controls.append({'variant': cls.__name__, 'remaining_duplicate_hint_preserved': True,
                                   'remaining_extractions': output, 'drained': True})

    random_controls = []
    for predictor in ('constant', 'identity', 'binned'):
        for seed in (3, 17, 101):
            for cls in (upstream.OnlineSL, MapCleanup):
                rng = random.Random(seed)
                upstream.np.random.seed(seed)
                queue, oracle, inserted, extracted = cls(), [], 0, 0
                for _ in range(256):
                    if oracle and (len(oracle) == 32 or rng.random() < 0.45):
                        pop(queue, oracle)
                        extracted += 1
                    else:
                        value = rng.randrange(16)
                        rank = 0 if predictor == 'constant' else value if predictor == 'identity' else value // 4
                        queue.insert(value, rank)
                        heapq.heappush(oracle, value)
                        inserted += 1
                while oracle:
                    pop(queue, oracle)
                    extracted += 1
                check(queue.isEmpty() and inserted == extracted, 'Random duplicate trace did not drain')
                if cls is MapCleanup:
                    check(not queue.sl.nodes, 'Cleanup map is nonempty after drain')
                random_controls.append({'variant': cls.__name__, 'predictor': predictor,
                                        'seed': seed, 'insertions': inserted, 'extractions': extracted, 'passed': True})
    source_after = hashlib.sha256(args.upstream.read_bytes()).hexdigest()
    check(source_before == source_after, 'Upstream source changed')
    result = {'status': 'passed', 'upstream_repository': 'Ziyad-Benomar/Learning-augmented-priority-queues',
              'upstream_commit': COMMIT, 'upstream_skiplist_sha256': source_before,
              'upstream_source_unchanged': True, 'reproducer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'scope': 'Correctness and container cardinality only; no timing, allocation, process-leak or asymptotic claim.',
              'unique_bounded_live_trace': unique, 'minimal_duplicate_control': duplicate_controls,
              'random_duplicate_traces': random_controls, 'extracted_node_pointer_checks': pointer_checks,
              'cleanup_scope': 'Final-occurrence node-map deletion only; original next/prev pointers remain intact. Random controls use deterministic monotone predictions and do not establish arbitrary-advice duplicate correctness.'}
    text = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text)


if __name__ == '__main__':
    main()
