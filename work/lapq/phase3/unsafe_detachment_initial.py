"""Occurrence-preserving bookkeeping ablations; no performance execution.

Uses frozen phase-two integer-indexed original methods unchanged. The linked
rank bucket preserves append order, last-element hints and first-equal removal.
Cleanup does not group keys or change any live skiplist node. The validity
contract is finite numeric keys with frozen deterministic monotone predictions;
the original artifact's arbitrary-advice duplicate failures remain out of scope.
"""
from collections import deque
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'phase2'))
from queues import index_only_queue, make_queue


class _Occurrence:
    __slots__ = ('value', 'prev', 'next')

    def __init__(self, value, previous):
        self.value = value
        self.prev = previous
        self.next = None


class OccurrenceBucket:
    """List semantics needed by OnlineSL, with direct first-equal removal.

Expected O(1) append, tail access and remove by numeric value, subject to Python
dictionary hashing. Space is O(live occurrences + distinct values). This changes
container allocation/hash costs; it is not a cost-free substitute for list.remove.
"""
    __slots__ = ('first', 'last', '_by_value', '_length')

    def __init__(self, values=()):
        self.first = self.last = None
        self._by_value = {}
        self._length = 0
        for value in values:
            self.append(value)

    def append(self, value):
        node = _Occurrence(value, self.last)
        if self.last is None:
            self.first = node
        else:
            self.last.next = node
        self.last = node
        handles = self._by_value.get(value)
        if handles is None:
            handles = deque()
            self._by_value[value] = handles
        handles.append(node)
        self._length += 1

    def remove(self, value):
        handles = self._by_value.get(value)
        if not handles:
            raise ValueError('value is absent from occurrence bucket')
        node = handles.popleft()
        if not handles:
            del self._by_value[value]
        if node.prev is None:
            self.first = node.next
        else:
            node.prev.next = node.next
        if node.next is None:
            self.last = node.prev
        else:
            node.next.prev = node.prev
        node.prev = node.next = None
        self._length -= 1

    def __len__(self):
        return self._length

    def __getitem__(self, index):
        if self._length == 0:
            raise IndexError('empty occurrence bucket')
        if index == -1:
            return self.last.value
        if index == 0:
            return self.first.value
        raise IndexError('only head/tail access is supported')

    def __iter__(self):
        node = self.first
        while node is not None:
            yield node.value
            node = node.next

    def validate(self):
        nodes, previous, by_value = [], None, {}
        node = self.first
        while node is not None:
            if node.prev is not previous:
                raise AssertionError('nonreciprocal occurrence links')
            nodes.append(node)
            by_value.setdefault(node.value, []).append(node)
            previous, node = node, node.next
        if previous is not self.last or len(nodes) != self._length:
            raise AssertionError('occurrence endpoint/length mismatch')
        if set(by_value) != set(self._by_value):
            raise AssertionError('occurrence value-index keys mismatch')
        for value, handles in self._by_value.items():
            if list(handles) != by_value[value]:
                raise AssertionError('occurrence handle order mismatch')


class _BucketMap(dict):
    """Convert the original method's newly assigned [] into the new container."""
    def __init__(self, values):
        super().__init__()
        for rank, items in values.items():
            self[rank] = items

    def __setitem__(self, rank, values):
        super().__setitem__(rank, values if isinstance(values, OccurrenceBucket)
                            else OccurrenceBucket(values))


METHODS = (
    'occ_list_retain',
    'occ_list_map',
    'occ_list_detach',
    'occ_linked_retain',
    'occ_linked_detach',
    'grouped_compact_reference',
)
OCCURRENCE_METHODS = METHODS[:-1]


class OccurrenceQueue:
    def __init__(self, method, rank_limit, count=False):
        if method not in OCCURRENCE_METHODS:
            raise ValueError(method)
        self.method = method
        _, self.bucket_policy, self.cleanup = method.split('_')
        self.adapter = index_only_queue('bitmap', rank_limit, count=count)
        self.queue = self.adapter.queue
        self.sl = self.queue.sl
        if self.bucket_policy == 'linked':
            self.queue.rankVal = _BucketMap(self.queue.rankVal)

    def insert(self, value, predicted_rank=0):
        return self.queue.insert(value, predicted_rank)

    def extractMin(self):
        # Lookup adds no ordering comparison. The exact original extraction and
        # all original valRank list work still execute in every occurrence arm.
        removed = self.sl.findMin() if self.cleanup == 'detach' else None
        value = self.queue.extractMin()
        if self.cleanup != 'retain' and value not in self.queue.valRank:
            del self.sl.nodes[value]
        if removed is not None:
            removed.next.clear()
            removed.prev.clear()
            removed.height = 0
        return value

    def isEmpty(self):
        return self.queue.isEmpty()

    def stats(self):
        result = self.adapter.stats()
        result.update(kind=self.method, rank_bucket_policy=self.bucket_policy,
                      historical_node_cleanup=self.cleanup,
                      valRank_policy='original per-occurrence rank list unchanged',
                      grouping=False)
        return result


def make_ablation_queue(method, rank_limit, seed=None, count=False):
    """A supplied seed is validation convenience; timing must seed outside."""
    if seed is not None:
        import numpy as np
        np.random.seed(seed)
    if method == 'grouped_compact_reference':
        return make_queue('guided_bitmap_compact', seed=None, count=count,
                          rank_limit=rank_limit)
    return OccurrenceQueue(method, rank_limit, count=count)
