"""Classic monotone radix heap baseline for nonnegative integer priorities.

Only insertions >= last extracted value are supported, as satisfied by
Dijkstra with nonnegative integer weights. No prediction or training is used.
This pure-Python baseline is not claimed to be optimized native radix code.
"""


class RadixQueue:
    __slots__ = ('buckets', 'last', 'size')

    def __init__(self):
        self.buckets = [[]]
        self.last = 0
        self.size = 0

    def isEmpty(self):
        return self.size == 0

    def insert(self, value):
        # Integer-valued floats arise from the common Dijkstra initial 0.0;
        # conversion is exact for the validated <=2^53 study workloads.
        key = int(value)
        if key != value or key < self.last:
            raise ValueError('radix heap requires integer key >= last extraction')
        bucket = (key ^ self.last).bit_length()
        if bucket >= len(self.buckets):
            self.buckets.extend([] for _ in range(bucket+1-len(self.buckets)))
        self.buckets[bucket].append(key)
        self.size += 1

    def extractMin(self):
        if not self.size:
            raise IndexError('empty radix queue')
        if not self.buckets[0]:
            bucket = next(i for i in range(1,len(self.buckets)) if self.buckets[i])
            old = self.buckets[bucket]
            self.last = min(old)
            self.buckets[bucket] = []
            for key in old:
                self.buckets[(key ^ self.last).bit_length()].append(key)
        self.size -= 1
        return self.buckets[0].pop()
