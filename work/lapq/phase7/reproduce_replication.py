#!/usr/bin/env python3
"""Portable reproduction of frozen sorting comparison counts.

Quick subset: python3 reproduce_replication.py --mode quick --destination rerun-quick
Full plan:    python3 reproduce_replication.py --mode full --destination rerun-full
Verification only: python3 reproduce_replication.py --verify-only
Analyze a completed FULL rerun (with pinned reference inputs available):
  python3 analyze_replication.py --results rerun-full
    --output rerun-full/replication_summary.json
The full-grid analysis deliberately rejects quick-mode results.

The original executable hash is historical evidence, not a portable build
requirement. Every other original source/input/plan/evidence pin is enforced;
this runner compiles the pinned C++ source and records its own compiler/binary.
No original result, source, plan, or checksum file is modified. This script does
not run on import. Quick results are not the complete paper replication.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import platform
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback

from release_source import source_pin_path, verify_release_source

HERE = Path(__file__).resolve().parent
FREEZE_SHA256 = '5cf250d30ed87935eb0055137ff2a4305faa5858ed16e86c8adba026b3145a15'
HISTORICAL_BINARY = 'build/replication'
COMPILER_FLAGS = ['-O3', '-std=c++17', '-DNDEBUG', '-Wall', '-Wextra', '-Wpedantic']
QUICK_RULE = {'parameter_index': 20, 'repetition': 0,
              'regimes': ['class', 'decay'], 'sizes': [1000, 10000, 100000]}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def write_json(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.replace(path)


def verify_sources():
    require(sha(HERE/'frozen_protocol.json') == FREEZE_SHA256,
            'Frozen protocol differs; use an explicitly reviewed new reproduction protocol')
    frozen = json.loads((HERE/'frozen_protocol.json').read_text())
    require(frozen['status'] == 'frozen_before_primary_execution', 'Protocol is not the frozen primary plan')
    observed = {}
    require(HISTORICAL_BINARY in frozen['source_sha256'], 'Historical binary pin missing')
    for name, expected in frozen['source_sha256'].items():
        relative = PurePosixPath(name)
        require(not relative.is_absolute() and '..' not in relative.parts and '\\' not in name,
                'Unsafe source path in freeze')
        if name == HISTORICAL_BINARY:
            continue
        path = source_pin_path(name)
        require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(HERE),
                'Missing, symlinked, or escaping pinned file: ' + name)
        observed[name] = sha(path)
        require(observed[name] == expected, 'Source/input/evidence guard differs: ' + name)
    plan = json.loads((HERE/'experiment_plan.json').read_text())
    require(len(plan['conditions']) == frozen['condition_count'] == 3780,
            'Frozen condition count differs')
    require(plan['models'] == ['offline', 'online', 'dirty'] and plan['method_run_count'] == 11340,
            'Frozen model design differs')
    return frozen, plan, observed


def selected_conditions(plan, mode):
    if mode == 'full':
        return plan['conditions']
    chosen = [spec for spec in plan['conditions']
              if spec['parameter_index'] == QUICK_RULE['parameter_index']
              and spec['repetition'] == QUICK_RULE['repetition']]
    require(len(chosen) == 6 and {(s['regime'], s['n']) for s in chosen} ==
            {(r, n) for r in QUICK_RULE['regimes'] for n in QUICK_RULE['sizes']},
            'Predetermined quick subset coverage differs')
    return chosen


def build_engine(destination, compiler):
    resolved = shutil.which(compiler)
    require(resolved is not None, 'C++17 compiler not found: ' + compiler)
    folder = destination/'build'
    folder.mkdir()
    version = subprocess.run([resolved, '--version'], capture_output=True, text=True, timeout=30)
    (folder/'compiler-version.stdout.txt').write_text(version.stdout)
    (folder/'compiler-version.stderr.txt').write_text(version.stderr)
    binary = folder/('replication.exe' if sys.platform == 'win32' else 'replication')
    command = [resolved, *COMPILER_FLAGS, str(HERE/'replication.cpp'), '-o', str(binary)]
    metadata = {'status': 'building', 'started_utc': now(), 'argv': command,
                'compiler_path': resolved, 'compiler_version': version.stdout,
                'compiler_version_returncode': version.returncode,
                'source_sha256': sha(HERE/'replication.cpp'), 'flags': COMPILER_FLAGS,
                'release_source_revision': verify_release_source(),
                'scope': 'Fresh local build from the frozen source; no claim of binary identity across hosts.'}
    write_json(folder/'build.json', metadata)
    try:
        with (folder/'compile.stdout.txt').open('w') as out, (folder/'compile.stderr.txt').open('w') as err:
            completed = subprocess.run(command, stdout=out, stderr=err, timeout=180)
        metadata['returncode'] = completed.returncode
        require(completed.returncode == 0 and binary.is_file(), 'Compilation failed; see build logs')
        metadata.update(status='passed', binary_sha256=sha(binary), binary_bytes=binary.stat().st_size)
    except BaseException as exc:
        metadata.update(status='failed', error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        metadata['finished_utc'] = now()
        write_json(folder/'build.json', metadata)
    return binary, metadata


def output_reader(stream, replies):
    """A thread/queue works with subprocess pipes on Windows as well as POSIX."""
    try:
        for line in stream:
            replies.put(('line', line))
    except BaseException as exc:
        replies.put(('reader_error', repr(exc)))
    finally:
        replies.put(('eof', None))


def validate_response(result, request, spec):
    require(result.get('status') == 'ok', 'Engine returned a failure: ' + json.dumps(result))
    require(result.get('id') == request['id'] and result.get('model') == request['model']
            and result.get('n') == spec['n'], 'Engine response identity differs')
    require(result.get('sorted_ok') is True and result.get('height_count') == spec['n']
            and result.get('reference_only') is False, 'Engine correctness/height check failed')
    require(result.get('metric') == 'clean_key_comparisons_free_head', 'Unexpected comparison metric')
    for field in ('clean_comparisons', 'dirty_comparisons'):
        require(type(result.get(field)) is int and result[field] >= 0, 'Invalid comparator count')
    if request['model'] != 'dirty':
        require(result['dirty_comparisons'] == 0, 'Unexpected dirty comparator count')


def run_conditions(destination, binary, build, conditions, models, execution, timeout):
    from input_generation import generate_condition, engine_record
    replies = queue.Queue(maxsize=4)
    child = None
    reader = None
    last_condition = None
    current_request_id = None
    outputs = []
    start = time.monotonic()
    with (destination/'engine-stderr.txt').open('x') as err, \
         (destination/'conditions.jsonl').open('x', buffering=1) as records, \
         (destination/'raw-engine-output.jsonl').open('x', buffering=1) as raw:
        try:
            child = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=err, text=True, encoding='utf-8', bufsize=1)
            reader = threading.Thread(target=output_reader, args=(child.stdout, replies), daemon=True)
            reader.start()
            for index, spec in enumerate(conditions):
                condition = generate_condition(spec)
                last_condition = condition['metadata']
                outputs = []
                for model in models:
                    request = engine_record(condition, model)
                    current_request_id = request['id']
                    child.stdin.write(json.dumps(request, separators=(',', ':'))+'\n')
                    child.stdin.flush()
                    try:
                        kind, line = replies.get(timeout=timeout)
                    except queue.Empty:
                        raise RuntimeError('Engine response timeout: ' + current_request_id)
                    require(kind == 'line', 'Engine output ended or failed before response: '+current_request_id)
                    raw.write(line)
                    result = json.loads(line)
                    validate_response(result, request, spec)
                    outputs.append(result)
                    execution['completed_method_runs'] += 1
                records.write(json.dumps({'metadata': condition['metadata'], 'models': outputs,
                                          'passed': True}, separators=(',', ':'))+'\n')
                execution['completed_conditions'] = index+1
                if (index+1) % 30 == 0 or index+1 == len(conditions):
                    verify_sources()
                    require(sha(binary) == build['binary_sha256'], 'Local compiled binary changed')
                    execution['last_progress_utc'] = now()
                    write_json(destination/'execution.json', execution)
                    print(json.dumps({'completed_conditions': index+1, 'planned_conditions':len(conditions),
                                      'elapsed_progress_seconds_not_benchmark':round(time.monotonic()-start,1)}), flush=True)
            child.stdin.close()
            require(child.wait(timeout=30) == 0, 'Engine process returned nonzero status')
            reader.join(timeout=5)
            require(not reader.is_alive(), 'Engine output reader did not terminate')
            while not replies.empty():
                kind, line = replies.get_nowait()
                if kind == 'line':
                    raw.write(line)
                    raise RuntimeError('Unexpected trailing engine output')
                require(kind == 'eof', 'Engine output reader failed')
            verify_sources()
            require(sha(binary) == build['binary_sha256'], 'Local compiled binary changed')
            execution['status'] = 'passed'
        except BaseException as exc:
            if child is not None and child.poll() is None:
                child.kill()
                child.wait(timeout=30)
            execution['status'] = 'failed'
            failure = {'condition_index':execution['completed_conditions'], 'request_id':current_request_id,
                       'type':type(exc).__name__, 'message':str(exc), 'traceback':traceback.format_exc()}
            execution['failures'].append(failure)
            write_json(destination/'failed_condition.json', {'metadata':last_condition,
                        'completed_model_outputs':outputs, 'failure':failure})
            raise
        finally:
            execution['ended_utc'] = now()
            records.flush(); raw.flush()
            execution['condition_file_sha256'] = sha(destination/'conditions.jsonl')
            execution['raw_engine_file_sha256'] = sha(destination/'raw-engine-output.jsonl')
            write_json(destination/'execution.json', execution)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--mode', choices=('quick', 'full'), default='quick')
    parser.add_argument('--compiler', default='c++', help='GCC/Clang-compatible C++17 driver')
    parser.add_argument('--response-timeout', type=float, default=180)
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--compile-only', action='store_true')
    args = parser.parse_args()
    frozen, plan, sources = verify_sources()
    if args.verify_only:
        print(json.dumps({'status':'passed', 'frozen_protocol_sha256':FREEZE_SHA256,
                          'verified_source_input_evidence_files':len(sources),
                          'historical_binary_pin_exempted':HISTORICAL_BINARY,
                          'release_source_revision':verify_release_source(),
                          'experiments_run':False}, indent=2))
        return
    require(args.destination is not None, 'A fresh --destination is required')
    require(args.response_timeout > 0, 'Timeout must be positive')
    destination = args.destination.absolute()
    require(not destination.exists() and not destination.is_symlink(), 'Destination already exists; no overwrite/resume is supported')
    require(not destination.resolve().is_relative_to((HERE/'results').resolve()), 'Original results tree cannot be a reproduction destination')
    require(not HERE.is_relative_to(destination.resolve()), 'A source directory or ancestor cannot be the destination')
    from input_generation import require_numpy_version
    require_numpy_version()
    conditions = selected_conditions(plan, args.mode)
    destination.mkdir(parents=True, exist_ok=False)
    destination = destination.resolve()
    manifest = {'status':'prepared', 'created_utc':now(), 'mode':args.mode,
                'is_full_frozen_plan':args.mode=='full', 'quick_selection_rule':QUICK_RULE if args.mode=='quick' else None,
                'planned_conditions':len(conditions), 'planned_method_runs':len(conditions)*len(plan['models']),
                'models':plan['models'], 'conditions':conditions, 'original_freeze_sha256':FREEZE_SHA256,
                'verified_source_sha256':sources,
                'release_source_revision':verify_release_source(),
                'historical_binary_exemption':{'path':HISTORICAL_BINARY,'original_sha256':frozen['source_sha256'][HISTORICAL_BINARY],
                    'reason':'Platform-specific original binary; rebuild verified source and record the new executable instead.'},
                'portable_runner_sha256':sha(__file__), 'python':sys.version, 'platform':platform.platform(),
                'machine':platform.machine(), 'scope':'Comparison-count reproduction; elapsed progress is not a runtime benchmark. Quick mode is incomplete paper evidence.'}
    write_json(destination/'run-manifest.json', manifest)
    execution = {'status':'preparing','started_utc':now(),'freeze_sha256':FREEZE_SHA256,
                 'run_manifest_sha256':sha(destination/'run-manifest.json'),'portable_runner_sha256':sha(__file__),
                 'mode':args.mode,'full_paper_reproduction':args.mode=='full',
                 'release_source_revision':verify_release_source(),
                 'planned_conditions':len(conditions),'planned_method_runs':len(conditions)*len(plan['models']),
                 'completed_conditions':0,'completed_method_runs':0,'failures':[],
                 'python':sys.version,'platform':platform.platform(),
                 'scope':'Explicit recompiled comparison-count run; new hardware/compiler metadata retained.'}
    write_json(destination/'execution.json', execution)
    try:
        binary, build = build_engine(destination, args.compiler)
        verify_sources()
        execution['build'] = build
        if args.compile_only:
            execution.update(status='compiled_only', ended_utc=now())
            write_json(destination/'execution.json', execution)
            print(json.dumps({'status':'compiled_only','destination':str(destination),'experiments_run':False}))
            return
        execution['status'] = 'running'
        write_json(destination/'execution.json', execution)
        run_conditions(destination, binary, build, conditions, plan['models'], execution, args.response_timeout)
    except BaseException as exc:
        if execution['status'] not in ('failed', 'passed'):
            execution.update(status='failed', ended_utc=now())
            execution['failures'].append({'phase':'preparation','type':type(exc).__name__,'message':str(exc)})
            write_json(destination/'execution.json', execution)
        raise
    print(json.dumps({'status':execution['status'],'mode':args.mode,
                      'completed_conditions':execution['completed_conditions'],
                      'completed_method_runs':execution['completed_method_runs'],
                      'destination':str(destination)},indent=2))


if __name__ == '__main__':
    main()
