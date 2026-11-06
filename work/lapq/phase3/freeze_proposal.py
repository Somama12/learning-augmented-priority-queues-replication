#!/usr/bin/env python3
"""Freeze a proposed bookkeeping ablation from existing manifests; no benchmarks."""
import hashlib
import json
from pathlib import Path
import random

HERE = Path(__file__).resolve().parent
PHASE2 = HERE.parent / 'phase2'
ROOT = HERE.parents[2]
TARGETS = ('grid_8100', 'geometric_8192', 'ny', 'sparse_2048__lopsided')
METHODS = ('occ_list_retain', 'occ_list_map', 'occ_linked_retain',
           'occ_linked_map', 'grouped_compact_reference')
SEED = 90617341


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    destination = HERE / 'ablation_protocol.json'
    if destination.exists():
        raise RuntimeError('Proposal already frozen; preserve it and use a revision for changes')
    manifest_path = PHASE2 / 'workload_manifest.json'
    manifest = json.loads(manifest_path.read_text())
    preflight = json.loads((HERE / 'preflight_results.json').read_text())
    if preflight['status'] != 'passed':
        raise RuntimeError('A passing correctness preflight is required')
    cases, blocks = [], []
    for name in TARGETS:
        workload = manifest['workloads'][name]
        for train_index, split in enumerate(workload['splits']):
            for group in ('near', 'far'):
                query = next(q for q in split['queries'] if q['group'] == group)
                case = {'case_id': f'{name}_train{train_index}_{group}',
                        'workload': name, 'training_graph': workload['training_graph'],
                        'train_source': split['train_source'], 'query_source': query['source'],
                        'query_group': group, 'train_index': train_index,
                        'target_adjacency_sha256': workload['adjacency_sha256'],
                        'reference_distance_sha256': workload['reference_distances'][str(query['source'])]['sha256']}
                cases.append(case)
                for repeat in range(3 if name == 'ny' else 5):
                    identity = f"{case['case_id']}:{repeat}"
                    seed = int.from_bytes(hashlib.sha256(f'{SEED}:{identity}'.encode()).digest()[:4], 'big')
                    order = list(METHODS)
                    random.Random(seed ^ 0x1D3A).shuffle(order)
                    blocks.append({**case, 'repeat': repeat, 'queue_seed': seed,
                                   'method_order': order, 'block_id': identity})
    random.Random(SEED).shuffle(blocks)
    sources = [HERE / n for n in ('ablation_queues.py', 'preflight.py', 'freeze_proposal.py')]
    sources += [PHASE2 / n for n in ('queues.py', 'indexes.py', 'benchmark.py', 'workloads.py')]
    sources += [HERE.parent / 'data_loading.py', HERE.parent / 'upstream/skiplist.py']
    protocol = {
        'protocol_version': 1,
        'status': 'Frozen proposed design; correctness preflight passed; performance and memory experiments NOT executed',
        'decision_date': '2026-11-06',
        'preregistration': 'Private computational plan after examining phase-two results; not public preregistration or an independent confirmatory sample',
        'scope': 'Marginal bookkeeping costs at fixed occurrence-level skiplist semantics; grouped policy is an explicitly coupled secondary comparison',
        'selection_rationale': {
            'grid_8100': 'High duplicate pressure and large prior combined-repair gain',
            'geometric_8192': 'Mostly distinct numeric keys; prior repair showed little query-time gain',
            'ny': 'Large real integer graph and large historical-retention footprint',
            'sparse_2048__lopsided': 'Frozen-advisor saturation/collision stress, not a realism claim'},
        'methods': list(METHODS),
        'common_components': ['Frozen phase-two bitmap rank index and original skiplist methods',
                              'Original valRank per-occurrence lists in all four occurrence arms',
                              'Same scalar-distance to LIFO-vertex routing kernel',
                              'Same frozen linear advisor fitted from one common training-source heap trace',
                              'Same queue seed across methods within each block; seeds reset outside timers'],
        'factorial': {
            'cleanup': {'retain': 'Preserve original historical sl.nodes entries',
                        'map': 'Delete sl.nodes[value] only after valRank[value] disappears, meaning the last live occurrence was extracted; retain node links'},
            'rank_bucket': {'list': 'Original per-rank Python list',
                            'linked': 'Doubly linked occurrence sequence plus value-to-deque handles; append order, tail hint and first-equal removal preserved'},
            'not_changed': ['No duplicate grouping', 'No value-rank list replacement',
                            'No live representative retargeting', 'No pointer detachment',
                            'No changes to sampling or comparison decisions']},
        'contrasts': [
            {'id': 'map_cleanup_list', 'numerator': 'occ_list_map', 'denominator': 'occ_list_retain',
             'identifies': 'Conditional implementation effect of deleting exhausted-key dictionary entries with original list buckets and unchanged search/height traces',
             'does_not_identify': 'Full immediate object reclamation, link detachment or duplicate grouping'},
            {'id': 'map_cleanup_linked', 'numerator': 'occ_linked_map', 'denominator': 'occ_linked_retain',
             'identifies': 'Same map-entry cleanup effect conditional on linked occurrence buckets',
             'does_not_identify': 'A universal cleanup effect independent of bucket implementation or garbage collection'},
            {'id': 'bucket_replace_retained', 'numerator': 'occ_linked_retain', 'denominator': 'occ_list_retain',
             'identifies': 'Net effect of occurrence-preserving direct removal, including added hashing, handles and allocation, conditional on retained history',
             'does_not_identify': 'Pure asymptotic removal savings with all other machine costs erased'},
            {'id': 'bucket_replace_map', 'numerator': 'occ_linked_map', 'denominator': 'occ_list_map',
             'identifies': 'Same bucket-policy effect conditional on exhausted-key map cleanup',
             'does_not_identify': 'Duplicate compression; both arms retain one skiplist node per occurrence'},
            {'id': 'interaction', 'definition': '(linked_map/list_map) / (linked_retain/list_retain), formed within a paired block before source aggregation',
             'identifies': 'Multiplicative interaction of these two concrete implementation changes on the measured workload',
             'does_not_identify': 'Independent main effects when the interaction is non-negligible'},
            {'id': 'grouped_bridge', 'numerator': 'grouped_compact_reference', 'denominator': 'occ_linked_map',
             'identifies': 'Net difference between the existing grouped compact policy and a cleaned occurrence-preserving direct-bucket policy',
             'does_not_identify': 'Isolated duplicate grouping: live representative policy, node/height consumption, per-value metadata and link detachment also differ'}],
        'validity_contract': 'Finite numeric keys and fixed deterministic monotone rank prediction; the original arbitrary-advice duplicate defects are not claimed repaired',
        'preflight': preflight,
        'coupling_evidence': {
            'file': 'minimal_detachment_failure.json',
            'operations': ['insert(1,rank1)', 'insert(1,rank1)', 'extractMin()', 'insert(2,rank2)'],
            'finding': 'An extracted newest duplicate may remain the value-to-node hint representative while an older equal key remains live. Clearing its links invalidates subsequent insertion. Original and map-only controls pass this trace.',
            'excluded_attempts': ['unsafe_detachment_initial.py', 'unsafe_detachment_failure.json'],
            'interpretation': 'This is failure of our attempted transformation, not failure of the original queue on this trace'},
        'timing': {'planned_blocks': len(blocks), 'planned_method_queries': len(blocks) * len(METHODS),
                   'planned_source_cases': len(cases), 'small_repeats': 5, 'ny_repeats': 3,
                   'processes': 'Fresh process per paired block; shuffled block and method order',
                   'warmup': 'One validated warmup per method; warm RNG and class loading before measurement',
                   'gc': 'Disable cyclic GC during query as in phase two; force collection before methods and record cleanup separately',
                   'include': 'Queue construction, predictor lookup, shared routing and queue work',
                   'exclude': 'Graph loading, oracle checks, training/build, diagnostic tracing and forced post-query collection',
                   'clocks': ['process_time_ns', 'perf_counter_ns'],
                   'outliers': 'No time-based filtering; preserve all failures and stop attribution if semantic parity fails'},
        'memory': {'planned_graph_workers': len(TARGETS) * len(METHODS) * 2,
                   'source_subset': 'First training source and first far query for each of the four targets',
                   'gc_modes': ['enabled', 'disabled_during_query'],
                   'processes': 'Fresh process per target/method/GC mode; prewarm NumPy RNG and AST runtime before tracing',
                   'phases': ['training/build peak', 'query peak including retained advisor',
                              'empty queue alive before full collection', 'empty queue alive after full collection',
                              'after queue destruction and collection'],
                   'scope': 'Python traced allocations, graph/oracle excluded; record RSS high-water separately; never call a query-only peak whole-pipeline peak'},
        'bounded_live_diagnostic': {'status': 'Proposed, secondary; no timing interpretation',
                                    'methods': list(METHODS), 'insertions': [1000, 10000, 100000],
                                    'max_live': 128, 'prediction': 'constant0',
                                    'keys': 'unique increasing values; isolates exhausted-key history from duplicate grouping',
                                    'gc_modes': ['enabled', 'disabled_during_trace'],
                                    'planned_workers': len(METHODS) * 3 * 2},
        'analysis': {'unit': 'Per-source median paired repetition ratios; six source units per target grouped by three training sources',
                     'intervals': 'Exploratory training-cluster bootstrap only; too few clusters for broad confirmatory inference',
                     'report': 'Every contrast, both clocks, each target separately; no universal winner or full factor attribution to grouping',
                     'stop_rule': 'Any output, hint, operand, live-node or height mismatch invalidates the corresponding clean ablation; preserve and investigate before performance claims'},
        'performance_runner': 'Not implemented or executed in this preparation task; runner must enforce this frozen plan before data collection',
        'source_sha256': {str(p.relative_to(ROOT)): sha(p) for p in sources},
        'workload_manifest_sha256': sha(manifest_path),
        'protocol_seed': SEED, 'source_cases': cases, 'blocks': blocks}
    destination.write_text(json.dumps(protocol, indent=2) + '\n')
    print(json.dumps({'protocol': str(destination), 'cases': len(cases),
                      'blocks': len(blocks), 'planned_method_queries': len(blocks) * len(METHODS),
                      'performance_executed': False}))


if __name__ == '__main__':
    main()
