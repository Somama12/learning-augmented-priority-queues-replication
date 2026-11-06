#!/usr/bin/env python3
"""Separate memory-only bounded-live queue stress; never a timing benchmark.

Constant rank advice (universe1) holds prediction and index size fixed while
historical insertions grow. All numeric priorities are unique and increasing.
Each method/length runs in a fresh process. Imports and graph data are absent
from traced allocation counts. The maximum queue occupancy is always128.
"""
import argparse
import gc
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tracemalloc

HERE=Path(__file__).resolve().parent


def worker(kind, insertions, live_limit):
    from queues import make_queue
    # Compile/import the counter-free classes before tracing.
    warm=make_queue(kind,seed=101,count=False,rank_limit=0)
    warm.insert(0,0)
    if warm.extractMin()!=0:
        raise RuntimeError('warmup extraction failed')
    del warm
    gc.collect()
    tracemalloc.start()
    q=make_queue(kind,seed=101,count=False,rank_limit=0)
    next_extract=0
    for value in range(insertions):
        if value>=live_limit:
            actual=q.extractMin()
            if actual!=next_extract:
                raise RuntimeError(f'extraction mismatch: {actual} != {next_extract}')
            next_extract+=1
        q.insert(value,0)
    current_live, peak_live=tracemalloc.get_traced_memory()
    live_stats=q.stats()
    while not q.isEmpty():
        actual=q.extractMin()
        if actual!=next_extract:
            raise RuntimeError(f'drain mismatch: {actual} != {next_extract}')
        next_extract+=1
    if next_extract!=insertions:
        raise RuntimeError('queue did not drain every inserted value')
    # Full collection while the empty queue remains alive distinguishes objects
    # retained by queue metadata from unreachable Python garbage cycles.
    gc.collect()
    current_empty, peak_total=tracemalloc.get_traced_memory()
    empty_stats=q.stats()
    del q
    gc.collect()
    after_destroy=tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    return {'kind':kind,'insertions':insertions,'max_live':live_limit,
            'prediction':'constant0','rank_universe':1,'all_extractions_correct':True,
            'traced_current_at_live_limit':current_live,'traced_peak_at_live_limit':peak_live,
            'traced_current_empty_queue_alive':current_empty,'traced_peak_total':peak_total,
            'traced_after_queue_destroyed':after_destroy,
            'live_stats':live_stats,'empty_stats':empty_stats}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker',action='store_true')
    parser.add_argument('--kind',default='original_online')
    parser.add_argument('--insertions',type=int,default=1000)
    parser.add_argument('--live-limit',type=int,default=128)
    parser.add_argument('--run',action='store_true')
    args=parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.kind,args.insertions,args.live_limit)))
        return
    if not args.run:
        print('Prepared memory study. Run --run only after main timing ends.')
        return
    if (HERE/'.timing-runner.lock').exists():
        raise RuntimeError('Do not run memory stress during main timing')
    records=[]
    for n in [1000,10000,100000]:
        for kind in ['heapq','original_online','bitmap_online','guided_bitmap_compact']:
            call=[sys.executable,str(Path(__file__).resolve()),'--worker','--kind',kind,
                  '--insertions',str(n),'--live-limit','128']
            result=subprocess.run(call,capture_output=True,text=True,timeout=180)
            if result.returncode:
                records.append({'kind':kind,'insertions':n,'status':'error','stderr':result.stderr})
            else:
                records.append({'status':'ok',**json.loads(result.stdout)})
            print(kind,n,records[-1]['status'],flush=True)
    report={'design':'Unique increasing keys; constant rank0; maxlive128; all drain outputs verified; no runtime measurements.',
            'memory_scope':'Python traced allocations after imports/class compilation; empty queue remains alive aftergc; snapshot/stat bookkeeping adds small overhead.',
            'records':records,'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (HERE/'retention_results.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
