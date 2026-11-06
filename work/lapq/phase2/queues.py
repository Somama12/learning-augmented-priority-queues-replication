"""Timing-ready queue controls and explicit LAPQ artifact extensions.

The original queue definitions are loaded from pinned source. count=False strips
only assignments and increments of countComps/countDirtyComps in memory. It does
not rewrite search decisions or modify upstream files. Index-only adapters retain
the original node retention, duplicate handling and collision-list bookkeeping.

The separately labelled compact queue groups equal scalar keys, uses OrderedDict
rank buckets, removes expired node mappings, and detaches deleted nodes. Its hint
history and random-height consumption differ; it is NOT an index-only ablation.
All queues accept numeric scalar priorities. A shared distance->LIFO vertex map
in the graph harness is required for identical tie handling across queue types.
"""
import ast
from collections import OrderedDict
from functools import lru_cache
import hashlib
import heapq
import operator
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'deps'))
import numpy as np
from sortedcontainers import SortedList
try:
    from .indexes import BitmapPredecessorSet
except ImportError:
    from indexes import BitmapPredecessorSet

SOURCE = HERE.parent / 'upstream' / 'skiplist.py'
COUNTERS = {'countComps', 'countDirtyComps'}


class StripCounters(ast.NodeTransformer):
    @staticmethod
    def is_counter(node):
        return isinstance(node, ast.Attribute) and node.attr in COUNTERS
    def visit_Assign(self, node):
        if all(self.is_counter(target) for target in node.targets):
            return None
        return self.generic_visit(node)
    def visit_AugAssign(self, node):
        if self.is_counter(node.target):
            return None
        return self.generic_visit(node)


@lru_cache(maxsize=2)
def original_classes(count=False):
    parsed = ast.parse(SOURCE.read_text(), filename=str(SOURCE))
    names = {'Node', 'damagedCompare', 'SkipList', 'OnlineSL'}
    selected = [node for node in parsed.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    if {node.name for node in selected} != names:
        raise RuntimeError('pinned source definitions missing')
    module = ast.Module(body=selected, type_ignores=[])
    if not count:
        module = StripCounters().visit(module)
    ast.fix_missing_locations(module)
    namespace = {'np': np, 'SortedList': SortedList}
    exec(compile(module, str(SOURCE), 'exec'), namespace)
    return namespace['SkipList'], namespace['OnlineSL']


def _skiplist_stats(sl):
    count, seen, node = 0, set(), sl.head.getNext()
    cycle = False
    while node is not None:
        if id(node) in seen:
            cycle = True
            break
        seen.add(id(node))
        count += 1
        node = node.getNext()
    return {'reachable_nodes': count, 'level0_cycle': cycle,
            'retained_node_map_entries': len(sl.nodes),
            'max_skiplist_height': int(sl.maxHeight),
            'clean_comparisons': getattr(sl, 'countComps', None)}


class HeapQueue:
    def __init__(self):
        self.values = []
    def insert(self, value, predicted_rank=0):
        heapq.heappush(self.values, value)
    def extractMin(self):
        return heapq.heappop(self.values)
    def isEmpty(self):
        return not self.values
    def stats(self):
        return {'kind': 'heapq', 'live_entries': len(self.values),
                'container_shallow_bytes': sys.getsizeof(self.values)}


class SortedQueue:
    def __init__(self):
        self.values = SortedList()
    def insert(self, value, predicted_rank=0):
        self.values.add(value)
    def extractMin(self):
        return self.values.pop(0)
    def isEmpty(self):
        return not self.values
    def stats(self):
        return {'kind': 'sortedlist', 'live_entries': len(self.values)}


class RadixAdapter:
    def __init__(self):
        try:
            from .radix_queue import RadixQueue
        except ImportError:
            from radix_queue import RadixQueue
        self.queue = RadixQueue()
    def insert(self, value, predicted_rank=0):
        return self.queue.insert(value)
    def extractMin(self):
        return self.queue.extractMin()
    def isEmpty(self):
        return self.queue.isEmpty()
    def stats(self):
        return {'kind':'radix', 'live_entries':self.queue.size,
                'bucket_count':len(self.queue.buckets),
                'retained_bucket_entries':sum(len(bucket) for bucket in self.queue.buckets),
                'last_extracted':self.queue.last}


class SkipQueue:
    def __init__(self, count):
        self.sl = original_classes(count)[0]()
    def insert(self, value, predicted_rank=0):
        return self.sl.insert(value)
    def extractMin(self):
        return self.sl.extractMin()
    def isEmpty(self):
        return self.sl.isEmpty()
    def stats(self):
        result = _skiplist_stats(self.sl)
        result.update(kind='skiplist', live_entries=result['reachable_nodes'])
        return result


class OnlineQueue:
    def __init__(self, queue, kind):
        self.queue, self.kind = queue, kind
    def insert(self, value, predicted_rank=0):
        return self.queue.insert(value, predicted_rank)
    def extractMin(self):
        return self.queue.extractMin()
    def isEmpty(self):
        return self.queue.isEmpty()
    def stats(self):
        q = self.queue
        result = _skiplist_stats(q.sl)
        result.update(kind=self.kind, live_entries=result['reachable_nodes'],
                      live_rank_groups=len(q.rankVal)-1, live_value_groups=len(q.valRank),
                      rank_bucket_items=sum(len(values) for rank, values in q.rankVal.items() if rank != -np.inf),
                      value_rank_items=sum(len(values) for values in q.valRank.values()))
        if hasattr(q.veb, 'stats'):
            result['index'] = q.veb.stats()
        else:
            result['index'] = {'index_kind':'SortedList', 'size_excluding_sentinel':len(q.veb)-1}
        return result


def make_index(kind, universe, bitmap_mode='auto'):
    if kind == 'bitmap':
        return BitmapPredecessorSet(universe, mode=bitmap_mode)
    if kind == 'veb':
        try:
            from .veb_index import SparseVEBSet
        except ImportError:
            from veb_index import SparseVEBSet
        return SparseVEBSet(universe)
    raise ValueError('unknown integer index: '+kind)


def index_only_queue(index_kind, rank_limit, count=False, bitmap_mode='auto'):
    BaseSkip, BaseOnline = original_classes(count)
    limit = operator.index(rank_limit)
    if limit < 0:
        raise ValueError('rank_limit must be nonnegative')
    class IndexedOnline(BaseOnline):
        def __init__(self):
            # Same initial bookkeeping as upstream; replace only rank index.
            self.sl = BaseSkip()
            self.rankVal = {-np.inf: [-np.inf]}
            self.valRank = {}
            self.veb = make_index(index_kind, limit+1, bitmap_mode)
            if count:
                self.countComps = 0
        def getPredecessor(self, index, target):
            answer = index.predecessor_strict(target)
            return -np.inf if answer is None else answer
        def insert(self, value, predictedRank=0):
            rank = operator.index(predictedRank)
            if not 0 <= rank <= limit:
                raise ValueError('prediction outside configured rank universe')
            return super().insert(value, rank)
    return OnlineQueue(IndexedOnline(), index_kind+'_online')


class CompactGuidedQueue:
    """Grouped-key queue with live-only metadata and O(1) rank-bucket removal.

Repeated live occurrences of the same priority must receive the same rank.
Predictions may change after the last occurrence has been removed. Node grouping
changes height consumption versus upstream. No claim of trace equivalence.
"""
    def __init__(self, rank_limit, index_kind='bitmap', count=False, bitmap_mode='auto'):
        self.rank_limit = operator.index(rank_limit)
        if self.rank_limit < 0:
            raise ValueError('rank_limit must be nonnegative')
        self.sl = original_classes(count)[0]()
        self.index = make_index(index_kind, self.rank_limit+1, bitmap_mode)
        self.groups = {}  # priority -> [node, multiplicity, predicted rank]
        self.buckets = {}  # predicted rank -> OrderedDict(priority -> node)
        self.kind = 'guided_'+index_kind+'_compact'
    def insert(self, value, predicted_rank=0):
        rank = operator.index(predicted_rank)
        if not 0 <= rank <= self.rank_limit:
            raise ValueError('prediction outside configured rank universe')
        group = self.groups.get(value)
        if group is not None:
            if group[2] != rank:
                raise ValueError('compact queue requires a stable rank for duplicate live keys')
            group[1] += 1
            self.buckets[rank].move_to_end(value)
            return group[0]
        preceding_rank = self.index.predecessor_strict(rank)
        hint = self.sl.head if preceding_rank is None else next(reversed(self.buckets[preceding_rank].values()))
        node = self.sl.insertES(hint, value)
        if rank not in self.buckets:
            self.buckets[rank] = OrderedDict()
            self.index.add(rank)
        self.buckets[rank][value] = node
        self.groups[value] = [node, 1, rank]
        return node
    def extractMin(self):
        node = self.sl.findMin()
        if node is None:
            raise IndexError('extractMin from empty queue')
        value = node.value
        group = self.groups[value]
        if group[1] > 1:
            group[1] -= 1
            return value
        rank = group[2]
        self.sl.delete(node)
        del self.sl.nodes[value]
        del self.groups[value]
        del self.buckets[rank][value]
        if not self.buckets[rank]:
            del self.buckets[rank]
            self.index.remove(rank)
        # Original delete bypasses the node but retains old links. This labelled
        # repair drops those references to avoid retaining expired chains/cycles.
        node.next.clear()
        node.prev.clear()
        node.height = 0
        return value
    def isEmpty(self):
        return not self.groups
    def stats(self):
        result = _skiplist_stats(self.sl)
        result.update(kind=self.kind, live_entries=sum(group[1] for group in self.groups.values()),
                      live_distinct_keys=len(self.groups), live_rank_groups=len(self.buckets),
                      rank_bucket_items=sum(len(bucket) for bucket in self.buckets.values()),
                      index=self.index.stats(),
                      caveat='grouped duplicates and different hint/height history; not index-only ablation')
        return result


def make_queue(kind, seed=17, count=False, rank_limit=None, bitmap_mode='auto', index_kind=None):
    """rank_limit is inclusive; seed=None lets the harness seed outside timing."""
    if seed is not None:
        np.random.seed(seed)
    aliases = {'plain_skiplist':'skiplist', 'sortedlist_direct':'sortedlist',
               'original':'original_online', 'bitmap':'bitmap_online', 'veb':'veb_online',
               'compact':'guided_bitmap_compact', 'compact_online':'guided_bitmap_compact', 'bitmap_compact':'guided_bitmap_compact',
               'veb_compact':'guided_veb_compact'}
    kind = aliases.get(kind,kind)
    if kind in ('heapq','binary_heap'):
        return HeapQueue()
    if kind == 'sortedlist':
        return SortedQueue()
    if kind == 'skiplist':
        return SkipQueue(count)
    if kind == 'original_online':
        return OnlineQueue(original_classes(count)[1](), kind)
    if kind in ('bitmap_online','veb_online'):
        if rank_limit is None:
            raise ValueError('rank_limit is required for integer-index queues')
        return index_only_queue(index_kind or kind.split('_')[0],rank_limit,count,bitmap_mode)
    if kind in ('guided_bitmap_compact','guided_veb_compact'):
        if rank_limit is None:
            raise ValueError('rank_limit is required for compact queues')
        return CompactGuidedQueue(rank_limit,index_kind or kind.split('_')[1],count,bitmap_mode)
    if kind == 'radix':
        return RadixAdapter()
    raise ValueError('unknown queue kind: '+kind)


def implementation_notes():
    return {'upstream_skiplist_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            'counter_stripping':'only Assign/AugAssign targeting countComps/countDirtyComps; in-memory AST only',
            'numeric_key_contract':'scalar real keys supported by upstream subtraction; NaN and negative infinity unsupported',
            'index_only':'same original insert/extract methods, rank/value lists and retained nodes; bounded integer index + rank-domain validation only',
            'bitmap':'hierarchical 64-bit words, O(log_64 U); NOT vEB',
            'compact':'equal keys grouped, OrderedDict buckets, O(1) group removal, live-only nodes, detached deleted links; different hint and height history',
            'timing':'count=False removes original clean-comparison counters; caller must keep stats outside timed section',
            'randomness':'make_queue reseeds global numpy RNG unless seed=None; seed outside timers and execute sequentially for paired height streams',
            'stats':'object-size/live-entry diagnostics outside timing; no RSS claim'}
