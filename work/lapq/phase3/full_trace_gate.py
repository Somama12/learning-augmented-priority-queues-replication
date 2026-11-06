#!/usr/bin/env python3
"""Untimed streaming semantic gate on every planned source case.

Checks one fixed repeat-0 height seed per source case, not every timed seed.
Pointer-assignment, operand, hint, height and operation streams use stable node
creation IDs. Digests retain bounded diagnostic memory; no timings are claims.
"""
import argparse
import gc
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import traceback

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
P2=HERE.parent/'phase2'
PLAN=HERE/'ablation_protocol.json'
DEST=HERE/'trace_gate_results'

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def dump(path,value):
    path=Path(path)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2)+'\n')
    temporary.replace(path)

def fingerprints(plan):
    paths=list(plan['source_sha256'])+[str(PLAN.relative_to(ROOT)),str(Path(__file__).resolve().relative_to(ROOT))]
    result={name:sha(ROOT/name) for name in paths}
    for name,expected in plan['source_sha256'].items():
        if result[name]!=expected:raise RuntimeError('Frozen source changed: '+name)
    manifest=P2/'workload_manifest.json'
    if sha(manifest)!=plan['workload_manifest_sha256']:
        raise RuntimeError('Frozen workload manifest changed')
    return result

class Stream:
    def __init__(self):
        self.hash=hashlib.sha256()
        self.count=0
    def add(self,fmt,*values):
        self.hash.update(struct.pack('<'+fmt,*values))
        self.count+=1
    def result(self):return {'events':self.count,'sha256':self.hash.hexdigest()}

def traced_search(graph,source,advisor,method,seed,expected):
    from ablation_queues import make_ablation_queue
    import benchmark as b
    import queues
    import numpy as np
    streams={name:Stream() for name in ['operations','operands','hints','heights','node_creation','pointer_assignments']}
    Skip=queues.original_classes(False)[0]
    Node=Skip.__init__.__globals__['Node']
    original_init,original_set_next=Node.__init__,Node.setNext
    next_identifier=0
    def node_init(node,value=None,height=1):
        nonlocal next_identifier
        original_init(node,value,height)
        node._validation_id=next_identifier
        streams['node_creation'].add('Qdq',next_identifier,float(value),int(height))
        next_identifier+=1
    def set_next(node,next_node,height):
        streams['pointer_assignments'].add('Qqq',node._validation_id,
                                         -1 if next_node is None else next_node._validation_id,int(height))
        return original_set_next(node,next_node,height)
    Node.__init__,Node.setNext=node_init,set_next
    try:
        np.random.seed(seed)
        queue=make_ablation_queue(method,advisor.n,seed=None,count=False)
        sl=queue.sl
        original_compare,original_insert_es,original_height=sl.compare,sl.insertES,sl.sampleHeight
        def compare(a,c,dirty=False):
            streams['operands'].add('dd?',float(a),float(c),bool(dirty))
            return original_compare(a,c,dirty)
        def insert_es(node,value):
            streams['hints'].add('Qdqd',node._validation_id,float(node.value),int(node.height),float(value))
            return original_insert_es(node,value)
        def sample_height():
            height=original_height()
            streams['heights'].add('q',int(height))
            return height
        sl.compare,sl.insertES,sl.sampleHeight=compare,insert_es,sample_height
        class Wrapper:
            def __init__(self):self.inserts=self.extracts=0
            def insert(self,value,rank):
                queue.insert(value,rank)
                node=sl.findMin()
                streams['operations'].add('Bdqqq',1,float(value),int(rank),int(sl.head.height),
                                          -1 if node is None else node._validation_id)
                self.inserts+=1
            def extractMin(self):
                removed_id=sl.findMin()._validation_id
                value=queue.extractMin()
                node=sl.findMin()
                streams['operations'].add('Bdqqq',2,float(value),removed_id,int(sl.head.height),
                                          -1 if node is None else node._validation_id)
                self.extracts+=1
                return value
            def isEmpty(self):return queue.isEmpty()
        wrapper=Wrapper()
        answer=b.shared_kernel(graph,source,wrapper,advisor)
        b.verify(answer,expected)
        if wrapper.inserts!=wrapper.extracts:raise AssertionError('insertion/extraction mismatch')
        if method.endswith('_map') and sl.nodes:raise AssertionError('exhausted-key map not empty')
        output={'method':method,'status':'ok','insertions':wrapper.inserts,'extractions':wrapper.extracts,
                'distance_digest':b.digest(answer[0]),'streams':{k:v.result() for k,v in streams.items()},
                'post_query_stats':queue.stats()}
        del answer,wrapper,queue
        return output
    finally:
        Node.__init__,Node.setNext=original_init,original_set_next
        gc.collect()

def worker(index):
    from ablation_queues import OCCURRENCE_METHODS
    import benchmark as b
    import workloads
    plan=json.loads(PLAN.read_text())
    launch=fingerprints(plan)
    case=plan['source_cases'][index]
    block=min((x for x in plan['blocks'] if x['case_id']==case['case_id']),key=lambda x:x['repeat'])
    b.prepare_queue_runtime()
    graph=workloads.load(case['workload'])
    training=graph if case['training_graph']==case['workload'] else workloads.load(case['training_graph'])
    if workloads.graph_hash(graph)!=case['target_adjacency_sha256']:
        raise RuntimeError('Target adjacency differs from plan')
    expected=b.independent_oracle(graph,case['query_source'])
    if workloads._distance_hash(expected)!=case['reference_distance_sha256']:
        raise RuntimeError('Independent target oracle differs from frozen reference')
    trained=b.native_heap(training,case['train_source'],collect_keys=True)
    b.verify(trained,b.independent_oracle(training,case['train_source']))
    advisor=b.Advisor('linear',trained[3])
    del trained
    gc.collect()
    records=[]
    for method in OCCURRENCE_METHODS:
        try:record=traced_search(graph,case['query_source'],advisor,method,block['queue_seed'],expected)
        except Exception as error:
            record={'method':method,'status':'error','error':repr(error),'traceback':traceback.format_exc()}
        records.append(record)
        print(case['case_id'],method,record['status'],file=sys.stderr,flush=True)
    comparison_keys=['insertions','extractions','distance_digest','streams']
    ref=records[0]
    matching=(all(x['status']=='ok' for x in records) and
              all({k:x[k] for k in comparison_keys}=={k:ref[k] for k in comparison_keys} for x in records))
    completion=fingerprints(plan)
    return {'case':case,'queue_seed':block['queue_seed'],'seed_repeat':block['repeat'],
            'status':'passed' if matching and launch==completion else 'failed',
            'records':records,'all_streams_equal':matching,'source_sha256_at_launch':launch,
            'source_sha256_at_completion':completion,
            'scope':'One repeat-0 seed per planned source case; operations, operands, hints, node creations, sampled heights, pointer assignments; not every timed seed or an exhaustive correctness proof.'}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker',type=int)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    if args.worker is not None:
        try:print(json.dumps(worker(args.worker)))
        except Exception as e:
            print(json.dumps({'status':'error','error':repr(e),'traceback':traceback.format_exc()}))
            raise SystemExit(1)
        return
    if not args.run:
        print('Prepared streaming semantic gate; --run executes untimed validation.')
        return
    if (HERE/'.ablation-runner.lock').exists() or (P2/'.timing-runner.lock').exists():
        raise RuntimeError('Do not validate concurrently with timing')
    plan=json.loads(PLAN.read_text());frozen=fingerprints(plan)
    DEST.mkdir(exist_ok=True)
    records=[]
    for index,case in enumerate(plan['source_cases']):
        path=DEST/f'case-{index:02}.json'
        if path.exists():
            if not args.resume:raise RuntimeError('Prior gate record exists; review before --resume')
            record=json.loads(path.read_text())
            if record.get('source_sha256_at_launch')!=frozen:raise RuntimeError('Resumed gate source mismatch')
        else:
            result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--worker',str(index)],
                                  capture_output=True,text=True,timeout=1800)
            (DEST/f'case-{index:02}.stderr.txt').write_text(result.stderr)
            (DEST/f'case-{index:02}.stdout.txt').write_text(result.stdout)
            try:record=json.loads(result.stdout)
            except Exception:record={'status':'error','error':'invalid worker JSON','stdout':result.stdout}
            record['worker_returncode']=result.returncode
            dump(path,record)
        records.append(record)
        print(index+1,len(plan['source_cases']),case['case_id'],record['status'],flush=True)
        if record['status']!='passed':break
    passed=(len(records)==len(plan['source_cases']) and all(x['status']=='passed' for x in records))
    dump(HERE/'full_trace_gate_results.json',{'status':'passed' if passed else 'failed',
         'planned_source_cases':len(plan['source_cases']),'observed_source_cases':len(records),
         'variant_executions':sum(len(x.get('records',[])) for x in records),
         'protocol_sha256':sha(PLAN),'source_sha256':frozen,
         'scope':'Untimed stream equivalence, one repeat-0 height seed per source case. Finite validation, not all timed seeds or universal proof.',
         'cases':[{'file':str((DEST/f'case-{i:02}.json').relative_to(ROOT)),
                   'sha256':sha(DEST/f'case-{i:02}.json'),'status':x['status']} for i,x in enumerate(records)]})
    if not passed:raise SystemExit(1)

if __name__=='__main__':main()
