"""Hierarchical 64-bit bitmap set for bounded nonnegative integer ranks.

Each parent bit marks a nonempty child word. Updates and strict predecessor
search visit O(log_64(universe)) levels. This is NOT a van Emde Boas tree.
Dense storage is used for modest universes; sparse dictionaries avoid allocating
an array proportional to very large universes. Memory statistics are Python
object-size estimates, not RSS or allocator measurements.
"""
import operator
import sys


class BitmapPredecessorSet:
    WORD_BITS = 64
    AUTO_DENSE_WORD_LIMIT = 262144

    def __init__(self, universe, mode='auto'):
        self.universe = operator.index(universe)
        if self.universe <= 0:
            raise ValueError('universe must be positive')
        if mode not in ('auto', 'dense', 'sparse'):
            raise ValueError('mode must be auto, dense, or sparse')
        sizes, words = [], []
        bits = self.universe
        while True:
            sizes.append(bits)
            word_count = (bits + 63) >> 6
            words.append(word_count)
            if word_count == 1:
                break
            bits = word_count
        self._bit_sizes = sizes
        self._word_counts = words
        self.mode = ('dense' if sum(words) <= self.AUTO_DENSE_WORD_LIMIT else 'sparse') if mode == 'auto' else mode
        if self.mode == 'dense' and sum(words) > 8_000_000:
            raise ValueError('requested dense bitmap is too large; use sparse mode')
        self._sparse = self.mode == 'sparse'
        self._levels = [dict() if self._sparse else [0] * count for count in words]
        self._size = 0

    def __len__(self):
        return self._size

    def __bool__(self):
        return self._size != 0

    def _word(self, level, word_index):
        values = self._levels[level]
        return values.get(word_index, 0) if self._sparse else values[word_index]

    def _set_word(self, level, word_index, value):
        values = self._levels[level]
        if self._sparse and value == 0:
            values.pop(word_index, None)
        else:
            values[word_index] = value

    def _key(self, value):
        key = operator.index(value)
        if not 0 <= key < self.universe:
            raise ValueError('rank is outside the bounded universe')
        return key

    def __contains__(self, value):
        try:
            key = operator.index(value)
        except TypeError:
            return False
        if key < 0 or key >= self.universe:
            return False
        return bool(self._word(0, key >> 6) & (1 << (key & 63)))

    def add(self, value):
        key = self._key(value)
        index, mask = key >> 6, 1 << (key & 63)
        word = self._word(0, index)
        if word & mask:
            return False
        self._size += 1
        for level in range(len(self._levels)):
            index, mask = key >> 6, 1 << (key & 63)
            word = self._word(level, index)
            self._set_word(level, index, word | mask)
            if word:
                break
            key = index
        return True

    def discard(self, value):
        try:
            key = operator.index(value)
        except TypeError:
            return False
        if not 0 <= key < self.universe:
            return False
        index, mask = key >> 6, 1 << (key & 63)
        if not self._word(0, index) & mask:
            return False
        self._size -= 1
        for level in range(len(self._levels)):
            index, mask = key >> 6, 1 << (key & 63)
            word = self._word(level, index) & ~mask
            self._set_word(level, index, word)
            if word:
                break
            key = index
        return True

    def remove(self, value):
        if not self.discard(value):
            raise KeyError(value)

    def _predecessor_at(self, level, inclusive_bound):
        if inclusive_bound < 0:
            return None
        index, offset = inclusive_bound >> 6, inclusive_bound & 63
        eligible = self._word(level, index) & ((1 << (offset + 1)) - 1)
        if eligible:
            return (index << 6) + eligible.bit_length() - 1
        if level + 1 == len(self._levels):
            return None
        previous_word = self._predecessor_at(level + 1, index - 1)
        if previous_word is None:
            return None
        word = self._word(level, previous_word)
        return (previous_word << 6) + word.bit_length() - 1

    def predecessor_strict(self, value):
        """Largest member < value, or None; query bounds may lie outside U."""
        bound = operator.index(value)
        if not self._size or bound <= 0:
            return None
        return self._predecessor_at(0, min(bound, self.universe) - 1)

    def minimum(self):
        if not self._size:
            return None
        word_index = 0
        for level in range(len(self._levels) - 1, -1, -1):
            word = self._word(level, word_index)
            lowest = (word & -word).bit_length() - 1
            word_index = (word_index << 6) + lowest
        return word_index

    def maximum(self):
        return self.predecessor_strict(self.universe)

    min = minimum
    max = maximum

    def stats(self):
        # Avoid double-counting shared zero/int objects. Dictionary capacity is
        # included; allocator fragmentation and global class objects are not.
        seen = set()
        def size(value):
            if id(value) in seen:
                return 0
            seen.add(id(value))
            amount = sys.getsizeof(value)
            if isinstance(value, dict):
                amount += sum(size(k) + size(v) for k, v in value.items())
            elif isinstance(value, (list, tuple)):
                amount += sum(size(item) for item in value)
            return amount
        memory = sys.getsizeof(self) + size(self.__dict__)
        nonzero = [sum(bool(v) for v in values.values()) if self._sparse else sum(bool(v) for v in values)
                   for values in self._levels]
        return {'index_kind': 'hierarchical_64bit_bitmap', 'universe': self.universe,
                'mode': self.mode, 'size': self._size, 'levels': len(self._levels),
                'word_capacity_by_level': list(self._word_counts),
                'nonzero_words_by_level': nonzero, 'python_bytes_estimate': memory,
                'memory_scope': 'reachable Python objects owned/referenced by index; shared objects counted once; not RSS',
                'complexity': 'O(log_64 U) predecessor/add/remove; NOT a van Emde Boas implementation'}


HierarchicalBitmapSet = BitmapPredecessorSet
