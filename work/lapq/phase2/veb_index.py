"""Sparse recursive van Emde Boas-style set, with <=64-key bitmap leaves.

This is an engineering baseline, not a new data structure. The universe is
rounded to a power of two; high/low decomposition halves its bit width.
Lazy Python dict clusters avoid a dense O(U) allocation. Hash table accesses
have expected, rather than worst-case, constant cost. Empty clusters are freed.
"""


class _VEB:
    __slots__ = ('bits', 'lower_bits', 'mask', 'minimum', 'maximum', 'bitmap', 'summary', 'clusters')

    def __init__(self, bits):
        self.bits = bits
        self.lower_bits = bits // 2
        self.mask = (1 << self.lower_bits) - 1
        self.minimum = self.maximum = None
        self.bitmap = 0
        self.summary = None
        self.clusters = {}

    def contains(self, x):
        if self.bits <= 6:
            return bool(self.bitmap & (1 << x))
        if self.minimum is None:
            return False
        if x == self.minimum or x == self.maximum:
            return True
        cluster = self.clusters.get(x >> self.lower_bits)
        return cluster is not None and cluster.contains(x & self.mask)

    def insert(self, x):
        if self.bits <= 6:
            bit = 1 << x
            if self.bitmap & bit:
                return False
            self.bitmap |= bit
            self.minimum = (self.bitmap & -self.bitmap).bit_length() - 1
            self.maximum = self.bitmap.bit_length() - 1
            return True
        if self.minimum is None:
            self.minimum = self.maximum = x
            return True
        if x == self.minimum or x == self.maximum:
            return False
        if x < self.minimum:
            self.minimum, x = x, self.minimum
        high, low = x >> self.lower_bits, x & self.mask
        child = self.clusters.get(high)
        if child is None:
            child = self.clusters[high] = _VEB(self.lower_bits)
            if self.summary is None:
                self.summary = _VEB(self.bits - self.lower_bits)
            self.summary.insert(high)
        inserted = child.insert(low)
        if x > self.maximum:
            self.maximum = x
        return inserted

    def erase_existing(self, x):
        if self.bits <= 6:
            self.bitmap &= ~(1 << x)
            if self.bitmap:
                self.minimum = (self.bitmap & -self.bitmap).bit_length() - 1
                self.maximum = self.bitmap.bit_length() - 1
            else:
                self.minimum = self.maximum = None
            return
        if self.minimum == self.maximum:
            self.minimum = self.maximum = None
            return
        if x == self.minimum:
            first = self.summary.minimum
            x = (first << self.lower_bits) | self.clusters[first].minimum
            self.minimum = x
        high, low = x >> self.lower_bits, x & self.mask
        child = self.clusters[high]
        child.erase_existing(low)
        if child.minimum is None:
            del self.clusters[high]
            self.summary.erase_existing(high)
            if self.summary.minimum is None:
                self.summary = None
            if x == self.maximum:
                if self.summary is None:
                    self.maximum = self.minimum
                else:
                    last = self.summary.maximum
                    self.maximum = (last << self.lower_bits) | self.clusters[last].maximum
        elif x == self.maximum:
            self.maximum = (high << self.lower_bits) | child.maximum

    def predecessor(self, x):
        if self.minimum is None or x <= self.minimum:
            return None
        if x > self.maximum:
            return self.maximum
        if self.bits <= 6:
            smaller = self.bitmap & ((1 << x) - 1)
            return smaller.bit_length() - 1 if smaller else None
        high, low = x >> self.lower_bits, x & self.mask
        child = self.clusters.get(high)
        if child is not None and low > child.minimum:
            return (high << self.lower_bits) | child.predecessor(low)
        previous = self.summary.predecessor(high) if self.summary is not None else None
        if previous is None:
            return self.minimum
        return (previous << self.lower_bits) | self.clusters[previous].maximum


class SparseVEBSet:
    """Set of integers in [0, universe), with strict predecessor queries."""
    __slots__ = ('universe', 'root', '_size')

    def __init__(self, universe):
        if not isinstance(universe, int) or universe < 1:
            raise ValueError('positive integer universe required')
        self.universe = universe
        self.root = _VEB(max(1, (universe - 1).bit_length()))
        self._size = 0

    def __len__(self):
        return self._size

    def __contains__(self, value):
        return isinstance(value, int) and 0 <= value < self.universe and self.root.contains(value)

    def add(self, value):
        if not isinstance(value, int) or not 0 <= value < self.universe:
            raise ValueError('value outside integer universe')
        inserted = self.root.insert(value)
        self._size += inserted
        return inserted

    def discard(self, value):
        if value not in self:
            return False
        self.root.erase_existing(value)
        self._size -= 1
        return True

    def remove(self, value):
        if not self.discard(value):
            raise KeyError(value)

    def predecessor_strict(self, value):
        if not isinstance(value, int):
            raise TypeError('integer query required')
        return self.root.predecessor(value)

    def stats(self):
        nodes, clusters, words, depth = 0, 0, 0, 0
        stack = [(self.root, 1)]
        while stack:
            node, d = stack.pop()
            nodes += 1
            depth = max(depth, d)
            words += node.bits <= 6
            clusters += len(node.clusters)
            stack.extend((child, d+1) for child in node.clusters.values())
            if node.summary is not None:
                stack.append((node.summary, d+1))
        return {'kind':'sparse_veb','size':self._size,'universe':self.universe,
                'nodes':nodes,'cluster_entries':clusters,'leaf_words':words,'depth':depth}
