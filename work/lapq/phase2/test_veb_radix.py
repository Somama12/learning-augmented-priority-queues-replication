"""Differential checks of new non-learned index/baseline implementations."""
import bisect
import hashlib
import heapq
import json
from pathlib import Path
import random
from veb_index import SparseVEBSet
from radix_queue import RadixQueue


def main():
    checks=0
    universes=[1,2,3,63,64,65,127,128,129,4095,4096,4097,65536,300001,2**32,2**64]
    for universe in universes:
        for seed in range(12):
            rng=random.Random(seed*997+universe)
            index=SparseVEBSet(universe);reference=set()
            for _ in range(1500):
                value=rng.randrange(universe)
                if rng.random()<.6:
                    assert index.add(value)==(value not in reference)
                    reference.add(value)
                else:
                    assert index.discard(value)==(value in reference)
                    reference.discard(value)
                query=rng.randrange(-2,universe+3)
                ordered=sorted(reference);position=bisect.bisect_left(ordered,query)
                assert index.predecessor_strict(query)==(ordered[position-1] if position else None)
                assert len(index)==len(reference)
                assert (value in index)==(value in reference)
                checks+=1
            for value in list(reference):
                index.remove(value)
            assert len(index)==0 and index.predecessor_strict(universe) is None
            assert index.stats()['nodes']==1
    extractions=0
    for seed in range(100):
        rng=random.Random(seed);queue=RadixQueue();reference=[];last=0
        for _ in range(2000):
            if not reference or rng.random()<.65:
                value=last+rng.randrange(0,1<<rng.randrange(1,48))
                queue.insert(value);heapq.heappush(reference,value)
            else:
                last=heapq.heappop(reference)
                assert queue.extractMin()==last;extractions+=1
        while reference:
            assert queue.extractMin()==heapq.heappop(reference);extractions+=1
        assert queue.isEmpty()
    root=Path(__file__).resolve().parent
    output={'veb_mutation_query_checks':checks,'veb_universes':universes,
            'radix_differential_extractions':extractions,'all_passed':True,
            'sha256':{p:hashlib.sha256((root/p).read_bytes()).hexdigest()
                      for p in ['veb_index.py','radix_queue.py','test_veb_radix.py']}}
    (root/'veb_radix_validation.json').write_text(json.dumps(output,indent=2)+'\n')
    print(json.dumps(output))


if __name__=='__main__':main()
