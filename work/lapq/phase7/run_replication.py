#!/usr/bin/env python3
"""Run the frozen comparison-count plan sequentially; never report speed claims."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import platform
from pathlib import Path
import select
import subprocess
import sys
import time

from input_generation import generate_condition, engine_record

HERE=Path(__file__).resolve().parent

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',type=Path,default=HERE/'results')
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    freeze=json.loads((HERE/'frozen_protocol.json').read_text())
    assert freeze['status']=='frozen_before_primary_execution'
    for name,wanted in freeze['source_sha256'].items():
        assert sha(HERE/name)==wanted,'Source guard differs: '+name
    plan=json.loads((HERE/'experiment_plan.json').read_text())
    dest=args.destination;existing=[]
    if dest.exists():
        assert args.resume,'Destination exists; choose a new path or explicitly --resume'
        for line in (dest/'conditions.jsonl').read_text().splitlines():
            existing.append(json.loads(line))
        for record,spec in zip(existing,plan['conditions']):
            assert record['metadata']['spec']==spec and record['passed']
        assert len(existing)<=len(plan['conditions'])
        previous=json.loads((dest/'execution.json').read_text())
        assert previous['freeze_sha256']==sha(HERE/'frozen_protocol.json')
    else:dest.mkdir(parents=True)
    execution={'status':'running','started_utc':now(),'freeze_sha256':sha(HERE/'frozen_protocol.json'),
               'python':sys.version,'platform':platform.platform(),'machine':platform.machine(),
               'scope':'Sorting comparison counts only; elapsed progress is not a benchmark result.',
               'resumed_completed_conditions':len(existing),'planned_conditions':len(plan['conditions']),
               'planned_method_runs':plan['method_run_count'],'completed_conditions':len(existing),
               'failures':[]}
    (dest/'execution.json').write_text(json.dumps(execution,indent=2)+'\n')
    start=time.monotonic()
    with (dest/'engine-stderr.txt').open('a') as err, (dest/'conditions.jsonl').open('a',buffering=1) as records, (dest/'raw-engine-output.jsonl').open('a',buffering=1) as raw:
        child=subprocess.Popen([str(HERE/'build/replication')],stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,stderr=err,text=True,bufsize=1)
        try:
            for i,spec in enumerate(plan['conditions'][len(existing):],start=len(existing)):
                condition=generate_condition(spec);outputs=[]
                for model in plan['models']:
                    request=engine_record(condition,model)
                    child.stdin.write(json.dumps(request,separators=(',',':'))+'\n');child.stdin.flush()
                    if not select.select([child.stdout],[],[],180)[0]:
                        raise RuntimeError('Engine response timeout: '+request['id'])
                    line=child.stdout.readline()
                    if not line:raise RuntimeError('Engine terminated: '+request['id'])
                    raw.write(line)
                    result=json.loads(line)
                    assert result['status']=='ok',result
                    assert result['id']==request['id'] and result['model']==model and result['n']==spec['n']
                    assert result['sorted_ok'] and result['height_count']==spec['n'] and not result['reference_only']
                    assert result['clean_comparisons']>=0 and result['dirty_comparisons']>=0
                    if model!='dirty':assert result['dirty_comparisons']==0
                    outputs.append(result)
                records.write(json.dumps({'metadata':condition['metadata'],'models':outputs,'passed':True},separators=(',',':'))+'\n')
                execution['completed_conditions']=i+1
                if (i+1)%30==0:
                    for name,wanted in freeze['source_sha256'].items():
                        assert sha(HERE/name)==wanted,'Source changed during run: '+name
                    execution['last_progress_utc']=now()
                    (dest/'execution.json').write_text(json.dumps(execution,indent=2)+'\n')
                    print(json.dumps({'conditions':i+1,'total':len(plan['conditions']),
                                      'last':spec['condition_id'],'elapsed_progress_s':round(time.monotonic()-start,1)}),flush=True)
            child.stdin.close()
            assert child.wait(timeout=30)==0
            execution['status']='passed'
        except BaseException as exc:
            child.kill();child.wait()
            execution['status']='failed'
            execution['failures'].append({'condition_index':execution['completed_conditions'],
                                          'type':type(exc).__name__,'message':str(exc)})
            raise
        finally:
            execution['ended_utc']=now()
            execution['condition_file_sha256']=sha(dest/'conditions.jsonl')
            execution['raw_engine_file_sha256']=sha(dest/'raw-engine-output.jsonl')
            (dest/'execution.json').write_text(json.dumps(execution,indent=2)+'\n')
    print(json.dumps({'status':execution['status'],'completed_conditions':execution['completed_conditions']}),flush=True)

if __name__=='__main__':main()
