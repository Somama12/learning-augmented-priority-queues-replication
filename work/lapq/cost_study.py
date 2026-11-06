#!/usr/bin/env python3
"""Diagnostic cost-accounting replication of pinned LAPQ artifact.

Original Dijkstra and queue method definitions are AST-loaded unchanged. Thin
factories add controls and count order comparisons in advice/index searches.
Count runs are not runtime benchmarks; --timing adds unwrapped diagnostic runs.
"""
import argparse
import ast
from collections import Counter, defaultdict
import csv
import gc
import hashlib
import heapq
import json
import math
from pathlib import Path
import random
import signal
import statistics
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'deps'))
import numpy as np
import networkx as nx
from sortedcontainers import SortedList

PIN = '3915cc261e87949b2b0aa724673c2227492d1b08'
METHODS = ['binary_heap', 'plain_skiplist', 'sortedlist_direct', 'identity',
           'constant', 'linear', 'hist16', 'hist64', 'hist256', 'exact']
CURRENT = {}


def definitions(filename, names, namespace):
    path = HERE / 'upstream' / filename
    parsed = ast.parse(path.read_text(), filename=str(path))
    nodes = [n for n in parsed.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    if {n.name for n in nodes} != set(names):
        raise ValueError('missing upstream definitions: ' + filename)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)


NS = {'np': np, 'SortedList': SortedList,
      'heappush': heapq.heappush, 'heappop': heapq.heappop, 'heapify': heapq.heapify}
definitions('heaps.py', ['BinaryHeap', 'FibonacciHeap'], NS)
definitions('skiplist.py', ['Node', 'damagedCompare', 'SkipList', 'OnlineSL'], NS)
definitions('dijkstra.py', ['createPQ', 'insertInPQ', 'dijkstraPQ'], NS)
BinaryHeap, SkipList, OnlineSL = (NS[n] for n in ['BinaryHeap', 'SkipList', 'OnlineSL'])


def counted_number(meter, field):
    class Counted(float):
        def __lt__(self, other):
            meter[field] += 1
            return float.__lt__(self, other)
        def __gt__(self, other):
            meter[field] += 1
            return float.__gt__(self, other)
    return Counted


class ScanList(list):
    """Count visited items in list.remove, preserving its first-match semantics."""
    def __init__(self, values, meter, field):
        super().__init__(values)
        self.meter, self.field = meter, field
    def remove(self, value):
        for i, item in enumerate(self):
            self.meter[self.field] += 1
            if item is value or item == value:
                del self[i]
                return
        raise ValueError('list.remove(x): x not in list')


class ObservedOnlineSL(OnlineSL):
    def __init__(self, meter, instrumented):
        super().__init__()
        self.meter, self.instrumented = meter, instrumented
        self.Rank = counted_number(meter, 'auxiliary_order_comparisons')
        if instrumented:
            self.rankVal[-np.inf] = ScanList(self.rankVal[-np.inf], meter, 'rank_bucket_items_visited')
    def insert(self, val, predictedRank=0):
        rank = self.Rank(predictedRank) if self.instrumented else predictedRank
        super().insert(val, rank)
        if self.instrumented:
            if not isinstance(self.rankVal[rank], ScanList):
                self.rankVal[rank] = ScanList(self.rankVal[rank], self.meter, 'rank_bucket_items_visited')
            if not isinstance(self.valRank[val], ScanList):
                self.valRank[val] = ScanList(self.valRank[val], self.meter, 'value_rank_items_visited')


class ObservedBinaryHeap(BinaryHeap):
    def __init__(self, meter, instrumented):
        super().__init__()
        self.Number = counted_number(meter, 'heapq_actual_order_comparisons')
        self.instrumented = instrumented
    def insert(self, key):
        super().insert(self.Number(key) if self.instrumented else key)
    def extractMin(self):
        # Unwrap before Dijkstra's stale-distance checks to count only queue work.
        return float(super().extractMin())


class DirectSortedQueue:
    def __init__(self, meter, instrumented):
        self.values = SortedList()
        self.Number = counted_number(meter, 'direct_sortedlist_order_comparisons')
        self.instrumented = instrumented
        self.countComps = 0
    def isEmpty(self):
        return not self.values
    def insert(self, value):
        self.values.add(self.Number(value) if self.instrumented else value)
    def extractMin(self):
        return float(self.values.pop(0))


class TrackedQueue:
    def __init__(self, queue, meter):
        self.queue, self.meter, self.size = queue, meter, 0
    def insert(self, *args):
        answer = self.queue.insert(*args)
        self.size += 1
        self.meter['insertions'] += 1
        self.meter['peak_queue_entries'] = max(self.meter['peak_queue_entries'], self.size)
        return answer
    def extractMin(self):
        answer = self.queue.extractMin()
        self.size -= 1
        self.meter['extractions'] += 1
        return answer
    def isEmpty(self):
        return self.queue.isEmpty()
    @property
    def countComps(self):
        return self.queue.countComps


def queue_factory(pqID, predictions=None, keyNode=None):
    method, meter, instrumented = (CURRENT[k] for k in ['method', 'meter', 'instrumented'])
    if method == 'binary_heap':
        queue = ObservedBinaryHeap(meter, instrumented)
    elif method == 'plain_skiplist':
        queue = SkipList()
    elif method == 'sortedlist_direct':
        queue = DirectSortedQueue(meter, instrumented)
    else:
        queue = ObservedOnlineSL(meter, instrumented)
    return TrackedQueue(queue, meter)


NS['createPQ'] = queue_factory
dijkstra = NS['dijkstraPQ']


class Predictor:
    def __init__(self, name, keys, instrumented):
        self.name, self.instrumented = name, instrumented
        self.meter = Counter()
        self.calls = 0
        self.training_samples = len(keys)
        self.payload = None
        self.low = self.high = self.width = 0.0
        if name == 'exact':
            Number = counted_number(self.meter, 'advisor_order_comparisons')
            self.payload = SortedList(Number(x) for x in keys) if instrumented else SortedList(keys)
        elif name == 'linear' or name.startswith('hist'):
            self.low, self.high = min(keys), max(keys)
            self.width = self.high - self.low
            if name.startswith('hist'):
                self.bins = int(name[4:])
                counts = [0] * self.bins
                for x in keys:
                    idx = 0 if self.width == 0 else min(self.bins - 1, int((x-self.low) * self.bins/self.width))
                    counts[idx] += 1
                prefix = [0]
                for count in counts:
                    prefix.append(prefix[-1] + count)
                self.payload = prefix
        self.build_order_comparisons = self.meter['advisor_order_comparisons']
        self.meter.clear()
    def __bool__(self):
        return True
    def reset(self):
        self.meter.clear()
        self.calls = 0
    def bisect_left(self, key):
        self.calls += 1
        if self.name == 'exact':
            return self.payload.bisect_left(key)
        if self.name == 'identity':
            return key
        if self.name == 'constant':
            return 0
        if self.width <= 0 or key <= self.low:
            return 0
        if key >= self.high:
            return self.training_samples
        fraction = (key-self.low)/self.width
        if self.name == 'linear':
            return int(fraction * self.training_samples)
        scaled = fraction * self.bins
        idx = min(self.bins-1, int(scaled))
        return int(self.payload[idx] + (scaled-idx)*(self.payload[idx+1]-self.payload[idx]))
    def metadata(self):
        if self.name == 'exact':
            values = list(self.payload)
            size = sys.getsizeof(self.payload) + sys.getsizeof(values) + sum(sys.getsizeof(x) for x in values)
            slots = len(values)
        elif self.payload is not None:
            slots = len(self.payload)
            size = sys.getsizeof(self.payload) + sum(sys.getsizeof(x) for x in self.payload)
        else:
            slots, size = (2, 2*sys.getsizeof(self.low)) if self.name == 'linear' else (0, 0)
        return {'training_samples': self.training_samples, 'retained_numeric_slots': slots,
                'payload_bytes_approximate': size,
                'payload_bytes_caveat': ('rough partial Python payload estimate; counted exact models include comparison-wrapper overhead; NOT deployed-model memory, RSS, or a cross-model memory comparison'),
                'retained_numeric_slots_scope': 'core CDF payload; excludes scalar fields, index overhead and object metadata',
                'build_order_comparisons': self.build_order_comparisons if self.name=='exact' else None,
                'build_order_comparisons_scope': 'only exact SortedList construction measured; cheap-model min/max and histogram construction order checks unmeasured',
                'low': self.low, 'high': self.high}


def load_graph(path):
    graph = nx.read_graphml(path, node_type=int, force_multigraph=True)
    # GraphML numeric declarations normally convert length automatically.
    for u, v, key, attrs in graph.edges(keys=True, data=True):
        attrs['length'] = float(attrs['length'])
    if not graph.is_directed():
        graph = graph.to_directed()
    # Match the upstream city-mode graph[u][v][0]['length'] contract exactly.
    missing_zero = [(u, v) for u in graph for v in graph[u] if 0 not in graph[u][v]]
    if missing_zero:
        raise ValueError('GraphML has neighbor pairs without edge key 0: '+str(missing_zero[:3]))
    return graph


class AdjacencyGraphView:
    """Read-only city-mode interface over compact zero-based adjacency lists."""
    class Neighbors:
        def __init__(self, row):
            self.row = row
        def items(self):
            for target, weight in self.row:
                yield target, {0: {'length': weight}}
    def __init__(self, data):
        self.data = data
        self.nodes = range(data.n)
    def __len__(self):
        return self.data.n
    def __iter__(self):
        return iter(self.nodes)
    def __getitem__(self, vertex):
        return self.Neighbors(self.data.adj[vertex])
    def number_of_edges(self):
        return self.data.m


def reference_distances(graph, source):
    distance = {node: math.inf for node in graph}
    distance[source] = 0.0
    queue = [(0.0, source)]
    while queue:
        cost, u = heapq.heappop(queue)
        if cost != distance[u]:
            continue
        for v, edges in graph[u].items():
            candidate = cost + edges[0]['length']
            if candidate < distance[v]:
                distance[v] = candidate
                heapq.heappush(queue, (candidate, v))
    return distance


def distance_digest(distance):
    payload = '\n'.join(str(k)+':'+float(v).hex() for k,v in sorted(distance.items()))
    return hashlib.sha256(payload.encode()).hexdigest()


def alarm_handler(signum, frame):
    raise TimeoutError('per-run wall-clock limit exceeded')


signal.signal(signal.SIGALRM, alarm_handler)


def run_once(graph, source, method, predictor, seed, instrumented, timeout):
    meter = Counter()
    CURRENT.update(method=method, meter=meter, instrumented=instrumented)
    if predictor is not None:
        predictor.reset()
    np.random.seed(seed)
    start_wall, start_cpu = time.perf_counter_ns(), time.process_time_ns()
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        result = dijkstra(graph, source, predictions=predictor, pqID='BH' if predictor is None else 'OSL',
                          predGenID='sortedKeys', graphType='city', returnAllKeys=True)
        distances, queue, keys = result
        elapsed_cpu, elapsed_wall = time.process_time_ns()-start_cpu, time.perf_counter_ns()-start_wall
        if meter['insertions'] != meter['extractions'] or queue.size != 0:
            raise RuntimeError('queue became empty before all inserted entries were extracted')
        original = int(queue.countComps)
        if predictor is not None:
            meter.update(predictor.meter)
            meter['advisor_calls'] = predictor.calls
        if method == 'binary_heap':
            measured = meter['heapq_actual_order_comparisons'] if instrumented else None
        elif method == 'sortedlist_direct':
            measured = meter['direct_sortedlist_order_comparisons'] if instrumented else None
        else:
            measured = original
        accounted = None if measured is None else measured+meter['advisor_order_comparisons']+meter['auxiliary_order_comparisons']
        record = {'status': 'ok', 'original_queue_metric': original,
                  'queue_order_comparisons': measured, 'accounted_order_comparisons': accounted,
                  'elapsed_wall_ns': elapsed_wall, 'elapsed_cpu_ns': elapsed_cpu,
                  'distance_sha256': distance_digest(distances), 'trace_key_count': len(keys), **dict(meter)}
        return record, distances, keys
    except Exception as error:
        return {'status': 'error', 'error_type': type(error).__name__, 'error': str(error),
                'traceback': traceback.format_exc(), 'elapsed_wall_ns': time.perf_counter_ns()-start_wall,
                'elapsed_cpu_ns': time.process_time_ns()-start_cpu, **dict(meter)}, None, None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def choose_sources(graph, per_group, seed):
    rng = random.Random(seed)
    candidates = list(graph)
    rng.shuffle(candidates)
    for train in candidates[:min(30, len(candidates))]:
        distance = reference_distances(graph, train)
        ordered = sorted((v for v in graph if v != train and math.isfinite(distance[v])), key=lambda v:(distance[v],v))
        if len(ordered) >= max(30, 3*per_group):
            break
    else:
        raise ValueError('no training source reaches enough distinct held-out nodes')
    width = min(len(ordered)//3, max(4*per_group, len(ordered)//10))
    near = rng.sample(ordered[:width], per_group)
    far = rng.sample([v for v in ordered[-width:] if v not in near], per_group)
    used = {train, *near, *far}
    uniform = rng.sample([v for v in ordered if v not in used], per_group)
    sources = [('training_source',train)] + [('near',v) for v in near] + [('far',v) for v in far] + [('uniform',v) for v in uniform]
    return train, sources, distance


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--graph', default=str(HERE/'data'/'brussels.graphml'))
    parser.add_argument('--per-group', type=int, default=2)
    parser.add_argument('--seeds', default='17,29')
    parser.add_argument('--source-seed', type=int, default=20261106)
    parser.add_argument('--timeout', type=float, default=15.0)
    parser.add_argument('--timing', action='store_true')
    parser.add_argument('--methods', default=','.join(METHODS))
    parser.add_argument('--skip-training-source', action='store_true')
    parser.add_argument('--heldout-limit', type=int, default=0)
    parser.add_argument('--prefix', default=str(HERE/'cost_study_results'))
    args = parser.parse_args()
    graph_path = Path(args.graph)
    if graph_path.suffix == '.gr':
        from data_loading import load_dimacs
        graph = AdjacencyGraphView(load_dimacs(graph_path))
        max_weight = max(weight for row in graph.data.adj for _,weight in row)
        integer_bound = len(graph)*max_weight
        if integer_bound >= 2**53:
            raise ValueError('DIMACS integer path bound exceeds exact binary64 range used by counting adapters')
        integer_exactness = {'max_weight':max_weight, 'candidate_path_upper_bound':integer_bound,
                             'binary64_exact_integer_limit':2**53,'bound_is_below_limit':True}
    else:
        graph = load_graph(graph_path)
        integer_exactness = {'applicable':False,'reason':'original GraphML floating-point lengths'}
    methods_requested = args.methods.split(',')
    if not set(methods_requested) <= set(METHODS):
        raise ValueError('unknown method')
    seeds = [int(x) for x in args.seeds.split(',')]
    train, sources, train_reference = choose_sources(graph, args.per_group, args.source_seed)
    if args.heldout_limit:
        heldout = [(group,source) for group,source in sources if group!='training_source'][:args.heldout_limit]
        sources = [('training_source',train)] + heldout
    if args.skip_training_source:
        sources = [(group,source) for group,source in sources if group!='training_source']
    train_record, train_distances, keys = run_once(graph, train, 'binary_heap', None, seeds[0], True, args.timeout)
    if train_distances != train_reference:
        raise RuntimeError('training binary heap failed independent distance oracle: '+str(train_record))
    predictors, model_build = {}, {}
    for instrumented in ([True,False] if args.timing else [True]):
        for method in [m for m in methods_requested if m in METHODS[3:]]:
            wall, cpu = time.perf_counter_ns(), time.process_time_ns()
            predictor = Predictor(method, keys, instrumented)
            info = {'elapsed_cpu_ns':time.process_time_ns()-cpu, 'elapsed_wall_ns':time.perf_counter_ns()-wall,
                    **predictor.metadata()}
            predictors[(instrumented,method)] = predictor
            model_build[('counted:' if instrumented else 'timing:')+method] = info
    records = []
    rng = random.Random(92731)
    source_order = list(sources)
    rng.shuffle(source_order)
    for group,source in source_order:
        reference = reference_distances(graph, source)
        for instrumented in ([True,False] if args.timing else [True]):
            for seed in seeds:
                methods = list(methods_requested)
                rng.shuffle(methods)
                for method in methods:
                    predictor = predictors.get((instrumented,method))
                    record, distances, trace_keys = run_once(graph, source, method, predictor, seed, instrumented, args.timeout)
                    record.update(source=source, group=group, seed=seed, method=method,
                                  phase='counted' if instrumented else 'timing',
                                  training_distance=train_reference[source])
                    if distances is not None and distances != reference:
                        differences = [(v,distances[v],reference[v]) for v in reference if distances[v]!=reference[v]]
                        record.update(status='distance_mismatch', mismatch_count=len(differences), mismatch_examples=differences[:10])
                    records.append(record)
                    if len(graph)>50000:
                        print('Run',group,source,method,seed,record['status'],flush=True)
                    if record['status'] != 'ok':
                        print('FAIL', group, source, method, seed, record['status'], flush=True)
        print('Completed source',group,source,'records',len(records),flush=True)
    report = {
        'status':'diagnostic pilot, not a publication-grade timing benchmark', 'upstream_commit':PIN,
        'arguments':vars(args),
        'upstream_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (HERE/'upstream').glob('*.py')},
        'graph_path':str(graph_path),'graph_sha256':hashlib.sha256(graph_path.read_bytes()).hexdigest(),
        'graph_semantics':('DIMACS: all arcs preserved, including duplicate ordered arcs; lightweight items() iterator yields each arc independently.'
                           if graph_path.suffix=='.gr' else 'GraphML: preserve upstream city-mode neighbor order and edge-key-0 length; parallel nonzero-key edges excluded by original routine.'),
        'binary64_integer_exactness_check':integer_exactness,
        'nodes':len(graph),'edges':graph.number_of_edges(),'training_source':train,
        'sources':[{'group':g,'source':s,'training_distance':train_reference[s]} for g,s in sources],
        'seeds':seeds,'source_selection_seed':args.source_seed,'training_run':train_record,
        'model_build':model_build,'records':records,
        'post_run_verification':{'all_successful_queues_fully_drained':all(r.get('insertions')==r.get('extractions') for r in records if r['status']=='ok'),
                                 'audited_records':len(records)},
        'method_notes':{
            'loading':'AST-load unmodified queue and Dijkstra definitions; replace factory with documented counting adapters and controls.',
            'oracle':'independent heapq implementation, exact full distance-map equality; same edge-key-0 length semantics as original city-mode Dijkstra.',
            'training_cost':'Charge ONE baseline Dijkstra plus predictor construction. Original getKeyPredictions redundantly invokes getRanks before its collecting run; that extra run is not charged here. training_run elapsed times are instrumented even with --timing and must not be used as deployed training cost.',
            'binary_heap_original_metric':'Original countComps adds logarithmic expressions based on heap size; it is a proxy, not measured heapq comparisons. queue_order_comparisons uses wrapped keys to count actual heapq ordering operations.',
            'accounted_order_comparisons':'Lower-bound subtotal: measured queue clean order comparisons + exact-advisor ordering + auxiliary SortedList ordering. Excludes linear/hist predictor boundary checks, arithmetic, sentinel checks, dictionary/equality work and runtime overhead; not a total operation count.',
            'rank_bucket_items_visited':'Visited elements in original rankVal[rank].remove(value), counted with a semantics-preserving list subclass; reported separately from ordering comparisons.',
            'value_rank_items_visited':'Visited elements in valRank[value].remove(rank), separately counted.',
            'identity':'Artifact numeric control predictedRank=key. It moves exact ordering into SortedList; it is NOT the theorem bounded-integer-universe model and needs no training.',
            'sortedlist_direct':'Control uses SortedList directly as a priority queue; needs no training.',
            'histograms':'Equiwidth CDF with interpolation; B bins, O(1) lookup, trained from same key trace. low/high clamps apply; no held-out target fitting.',
            'source_groups':'One train source, disjoint near/far deciles (or larger for tiny graphs), and remaining uniform reachable held-out sources; randomized source and method execution order.',
            'timing':'Counted runs include instrumentation and are unsuitable for performance claims. Optional timing runs omit comparison wrappers and scan counting; original artifact queue counters/tracking remain. Host uncontrolled.',
            'scope':'Frozen graph, one train source, limited query sources and queue seeds. All failures retained; no upstream algorithm patch.'},
        'versions':{'python':sys.version,'numpy':np.__version__,'networkx':nx.__version__},
    }
    if graph_path.name.lower()=='brussels.graphml':
        report['released_artifact_not_figure_replication']='Shipped Brussels GraphML has 2268 nodes and 4393 raw arcs (4378 key-0 arcs); paper appendix figure labels Brussels 1380 nodes/2580 edges. Cause unverified.'
    fields = sorted(set().union(*(r.keys() for r in records)))
    with open(args.prefix+'.csv','w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader();writer.writerows(records)
    with open(args.prefix+'.json','w') as handle:
        json.dump(report,handle,indent=2)
    print('Saved',len(records),'runs;',dict(Counter(r['status'] for r in records)),flush=True)
    for method in METHODS:
        rs=[r for r in records if r['phase']=='counted' and r['method']==method and r['status']=='ok']
        if rs:
            print(method,'n',len(rs),'median original',statistics.median(r['original_queue_metric'] for r in rs),
                  'median accounted',statistics.median(r['accounted_order_comparisons'] for r in rs),
                  'median bucket visits',statistics.median(r.get('rank_bucket_items_visited',0) for r in rs),flush=True)


if __name__ == '__main__':
    main()
