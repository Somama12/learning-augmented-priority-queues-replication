#!/usr/bin/env python3
"""Correctness and semantic-parity tests; deliberately no timing assertions."""
import bisect
import heapq
import json
from pathlib import Path
import random
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from indexes import BitmapPredecessorSet
from queues import make_queue, original_classes, implementation_notes
import numpy as np
from veb_index import SparseVEBSet


def trace(seed, steps=200, monotone=False):
    rng = random.Random(seed)
    values, operations, last = [], [], 0
    for _ in range(steps):
        if not values or rng.random() < .61:
            key = (last + rng.randrange(20)) if monotone else rng.randrange(80)
            heapq.heappush(values, key)
            operations.append(('insert',key))
        else:
            last = heapq.heappop(values)
            operations.append(('extract',last))
    while values:
        operations.append(('extract',heapq.heappop(values)))
    return operations


class BitmapTests(unittest.TestCase):
    def test_boundaries_and_duplicates(self):
        for mode in ['dense','sparse']:
            for universe in [1,2,63,64,65,127,128,129,4095,4096,4097,65537]:
                index = BitmapPredecessorSet(universe,mode)
                members = sorted({0,universe-1,min(63,universe-1),min(64,universe-1)})
                self.assertIsNone(index.minimum())
                for key in members:
                    self.assertTrue(index.add(key))
                    self.assertFalse(index.add(key))
                for query in [-10,0,1,63,64,65,universe-1,universe,universe+7]:
                    position = bisect.bisect_left(members,query)
                    expected = members[position-1] if position else None
                    self.assertEqual(index.predecessor_strict(query),expected)
                self.assertEqual(index.minimum(),members[0])
                self.assertEqual(index.maximum(),members[-1])
                for key in members:
                    self.assertTrue(key in index)
                    index.remove(key)
                    self.assertFalse(index.discard(key))
                    with self.assertRaises(KeyError):
                        index.remove(key)
                self.assertEqual(len(index),0)
                self.assertIsNone(index.minimum())
                self.assertIsNone(index.maximum())
                self.assertFalse(index.discard(-1))
                self.assertFalse(index.discard(universe))
                self.assertTrue(all(x==0 for x in index.stats()['nonzero_words_by_level']))
                json.dumps(index.stats())

    def test_randomized_against_sorted_set(self):
        for universe in [1,3,64,65,4096,4097,100000,2**32,2**64]:
            modes = ['sparse'] if universe>100000 else ['dense','sparse']
            for mode in modes:
                rng = random.Random(8137+universe)
                index, reference = BitmapPredecessorSet(universe,mode), set()
                for step in range(1200):
                    key = rng.randrange(universe)
                    if rng.random() < .58:
                        self.assertEqual(index.add(key),key not in reference)
                        reference.add(key)
                    else:
                        if reference and rng.random()<.8:
                            key = rng.choice(tuple(reference))
                        self.assertEqual(index.discard(key),key in reference)
                        reference.discard(key)
                    self.assertEqual(len(index),len(reference))
                    ordered = sorted(reference)
                    for query in [key,rng.randrange(universe+2),0,universe+1]:
                        pos = bisect.bisect_left(ordered,query)
                        self.assertEqual(index.predecessor_strict(query),ordered[pos-1] if pos else None)
                    self.assertEqual(index.minimum(),ordered[0] if ordered else None)
                    self.assertEqual(index.maximum(),ordered[-1] if ordered else None)
                for key in list(reference):
                    index.remove(key)
                self.assertEqual(len(index),0)

    def test_auto_mode_and_domain(self):
        self.assertEqual(BitmapPredecessorSet(300001).mode,'dense')
        index = BitmapPredecessorSet(2**64)
        self.assertEqual(index.mode,'sparse')
        index.add(2**64-1)
        self.assertEqual(index.maximum(),2**64-1)
        with self.assertRaises(ValueError):
            BitmapPredecessorSet(0)
        with self.assertRaises(ValueError):
            index.add(-1)
        with self.assertRaises(ValueError):
            index.add(2**64)
        with self.assertRaises(TypeError):
            index.add(1.5)

    def test_bitmap_veb_same_operations(self):
        for universe in [1,65,4097,100000,2**32,2**64]:
            bitmap, veb = BitmapPredecessorSet(universe), SparseVEBSet(universe)
            rng = random.Random(universe+5923)
            live = set()
            for _ in range(1000):
                key = rng.randrange(universe)
                if rng.random()<.6:
                    self.assertEqual(bitmap.add(key),veb.add(key));live.add(key)
                else:
                    if live and rng.random()<.8:
                        key=rng.choice(tuple(live))
                    self.assertEqual(bitmap.discard(key),veb.discard(key));live.discard(key)
                for query in [0,key,universe,universe+1]:
                    self.assertEqual(bitmap.predecessor_strict(query),veb.predecessor_strict(query))
                self.assertEqual(len(bitmap),len(veb))


class QueueTests(unittest.TestCase):
    def run_checked(self, kind, operations, predictor, seed=17, count=False, limit=10000):
        queue = make_queue(kind,seed=seed,count=count,rank_limit=limit)
        live = []
        for action,value in operations:
            if action=='insert':
                queue.insert(value,predictor(value));heapq.heappush(live,value)
            else:
                self.assertFalse(queue.isEmpty(),(kind,seed,action,value))
                self.assertEqual(queue.extractMin(),heapq.heappop(live),(kind,seed,action,value))
            self.assertEqual(queue.isEmpty(),not live)
        self.assertTrue(queue.isEmpty())
        stats=queue.stats();json.dumps(stats)
        self.assertEqual(stats['live_entries'],0)
        self.assertFalse(stats.get('level0_cycle',False))
        return queue

    def test_all_variants_frozen_monotone_advice(self):
        kinds=['heapq','sortedlist','skiplist','original_online','bitmap_online','veb_online',
               'guided_bitmap_compact','guided_veb_compact']
        predictors=[lambda x:0,lambda x:7,lambda x:x//8,lambda x:x]
        for seed in range(12):
            operations=trace(seed)
            for predictor in predictors:
                for kind in kinds:
                    self.run_checked(kind,operations,predictor,seed=seed,limit=100)

    def test_compact_arbitrary_frozen_advice(self):
        for seed in range(30):
            rng=random.Random(seed)
            ranks={key:rng.randrange(17) for key in range(80)}
            for kind in ['guided_bitmap_compact','guided_veb_compact']:
                queue=self.run_checked(kind,trace(seed,300),ranks.__getitem__,seed=seed,limit=16)
                self.assertEqual(queue.stats()['retained_node_map_entries'],0)
                self.assertEqual(queue.stats()['live_rank_groups'],0)
                self.assertEqual(queue.stats()['index']['size'],0)

    def test_index_only_trace_parity_and_counter_stripping(self):
        operations=trace(12,350)
        for predictor in [lambda x:0,lambda x:x//5,lambda x:x]:
            traces={}
            for kind in ['original_online','bitmap_online','veb_online']:
                for count in [True,False]:
                    queue=make_queue(kind,seed=41,count=count,rank_limit=100)
                    snapshots=[]
                    for action,value in operations:
                        extracted=None
                        if action=='insert':queue.insert(value,predictor(value))
                        else:extracted=queue.extractMin()
                        sl=queue.queue.sl
                        values,heights=sl.getValsHeights()
                        snapshots.append((extracted,values,heights,getattr(sl,'countComps',None)))
                    traces[(kind,count)]=snapshots
            for kind in ['bitmap_online','veb_online']:
                self.assertEqual(traces[('original_online',True)],traces[(kind,True)])
            reference=[row[:3] for row in traces[('original_online',True)]]
            for snapshots in traces.values():
                self.assertEqual(reference,[row[:3] for row in snapshots])
            for kind in ['original_online','bitmap_online','veb_online']:
                self.assertTrue(all(row[3] is None for row in traces[(kind,False)]))

    def test_float_priorities_and_duplicate_group_lifecycle(self):
        operations=[('insert',1.25),('insert',1.25),('insert',2.5),('extract',1.25),
                    ('insert',0.125),('extract',0.125),('extract',1.25),('extract',2.5)]
        for kind in ['heapq','sortedlist','skiplist','original_online','bitmap_online','veb_online','compact_online']:
            self.run_checked(kind,operations,lambda x:int(4*x),limit=20)
        queue=make_queue('compact_online',rank_limit=5)
        node=queue.insert(2.0,1)
        queue.insert(2.0,1)
        self.assertEqual(queue.stats()['reachable_nodes'],1)
        self.assertEqual(queue.stats()['live_entries'],2)
        self.assertEqual(queue.extractMin(),2.0)
        self.assertEqual(queue.stats()['retained_node_map_entries'],1)
        with self.assertRaises(ValueError):queue.insert(2.0,2)
        self.assertEqual(queue.extractMin(),2.0)
        self.assertEqual(queue.stats()['retained_node_map_entries'],0)
        self.assertEqual(node.next,[]);self.assertEqual(node.prev,[])
        # A changed rank is permitted after all prior equal keys are gone.
        queue.insert(2.0,2);self.assertEqual(queue.extractMin(),2.0)

    def test_compact_empty_memory_and_domain_validation(self):
        queue=make_queue('compact_online',rank_limit=63)
        for value in range(1500):queue.insert(value,(value*13)%64)
        for value in range(1500):self.assertEqual(queue.extractMin(),value)
        stats=queue.stats()
        self.assertEqual(stats['retained_node_map_entries'],0)
        self.assertEqual(stats['rank_bucket_items'],0)
        self.assertEqual(stats['index']['size'],0)
        for kind in ['bitmap_online','veb_online','compact_online']:
            queue=make_queue(kind,rank_limit=8)
            with self.assertRaises(ValueError):queue.insert(3,9)
            with self.assertRaises(ValueError):queue.insert(3,-1)
            self.assertTrue(queue.isEmpty())

    def test_radix_monotone_scalar_interface(self):
        for seed in range(20):
            self.run_checked('radix',trace(seed,400,monotone=True),lambda x:0,seed=seed)

    def test_counter_stripped_classes_and_notes(self):
        for cls in original_classes(False):
            instance=cls()
            self.assertFalse(hasattr(instance,'countComps'))
        self.assertIn('NOT vEB',implementation_notes()['bitmap'])
        json.dumps(implementation_notes())

    def test_external_rng_seed_preserves_height_stream(self):
        for kind in ['original_online','bitmap_online','compact_online']:
            first=make_queue(kind,seed=91,rank_limit=100)
            for value in range(50):first.insert(value,value)
            sl_first=first.queue.sl if hasattr(first,'queue') else first.sl
            expected=sl_first.getValsHeights()
            np.random.seed(91)
            second=make_queue(kind,seed=None,rank_limit=100)
            for value in range(50):second.insert(value,value)
            sl_second=second.queue.sl if hasattr(second,'queue') else second.sl
            self.assertEqual(expected,sl_second.getValsHeights())


if __name__=='__main__':
    unittest.main(verbosity=2)
