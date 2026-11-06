#!/usr/bin/env python3
"""Run the frozen retention-only worker subset into a fresh destination."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n')


def now():
    return datetime.now(timezone.utc).isoformat()


def check_sources(root, expected):
    actual = {name: sha(root / name) for name in expected}
    if actual != expected:
        raise RuntimeError('Frozen source/input hash mismatch')
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    plan = read(args.plan)
    manifest_path = root / plan['source_manifest']
    if sha(manifest_path) != plan['source_manifest_sha256']:
        raise RuntimeError('Execution manifest differs from frozen wrapper plan')
    manifest = read(manifest_path)
    jobs = [j for j in manifest['jobs'] if j['kind'] == 'retention']
    if jobs != plan['jobs'] or len(jobs) != 30:
        raise RuntimeError('Retention jobs differ from frozen wrapper plan')
    native = read(root / 'work/lapq/phase2/second_host_native/native_experiment.json')
    if not native.get('all_passed') or len(native['cases']) != 42:
        raise RuntimeError('Complete native timing must finish before retention')
    check_sources(root, plan['source_hashes'])
    destination = root / plan['destination']
    destination.mkdir(parents=True, exist_ok=False)
    write(destination / 'frozen_wrapper_plan.json', plan)
    wrapper_path = Path(__file__).resolve()
    wrapper_hash = sha(wrapper_path)
    (destination / 'second_host_retention.py').write_bytes(wrapper_path.read_bytes())
    report = {'status': 'running', 'started_utc': now(), 'jobs': [],
              'plan_sha256': sha(args.plan), 'wrapper_sha256': wrapper_hash,
              'expected_workers': 30, 'source_hashes': plan['source_hashes'],
              'timeout_seconds': plan['worker_timeout_seconds'],
              'process_model': 'Fresh sequential subprocess per unchanged spec',
              'validation_provenance': plan['validation_provenance']}
    for ordinal, job in enumerate(jobs):
        folder = destination / job['job_id']
        folder.mkdir()
        spec_path = folder / 'spec.json'
        write(spec_path, job['spec'])
        before = check_sources(root, plan['source_hashes'])
        command = [sys.executable, str(root / plan['worker']), 'worker',
                   '--kind', 'retention', '--spec', str(spec_path),
                   '--manifest', str(manifest_path)]
        record = {'job_id': job['job_id'], 'spec': job['spec'], 'argv': command,
                  'started_utc': now(), 'ordinal': ordinal,
                  'source_hashes_before': before, 'errors': []}
        stdout, stderr = folder / 'stdout.txt', folder / 'stderr.txt'
        with stdout.open('w') as out, stderr.open('w') as err:
            try:
                child = subprocess.run(command, cwd=root, stdout=out, stderr=err,
                                       timeout=plan['worker_timeout_seconds'], check=False)
                record['returncode'] = child.returncode
                if child.returncode != 0:
                    record['errors'].append('nonzero worker exit code')
            except Exception:
                record['returncode'] = None
                record['errors'].append(traceback.format_exc())
        try:
            result = read(stdout)
            write(folder / 'result.json', result)
            if result.get('status') != 'ok' or result.get('spec') != job['spec']:
                record['errors'].append('worker status/spec mismatch')
            if result.get('all_extractions_correct') is not True:
                record['errors'].append('extraction correctness not verified')
            if result.get('extractions') != job['spec']['insertions']:
                record['errors'].append('extraction count mismatch')
            for field in ('python_traced_empty_queue_alive_before_gc_bytes',
                          'python_traced_empty_queue_alive_after_gc_bytes'):
                if not isinstance(result.get(field), int) or result[field] < 0:
                    record['errors'].append('missing/invalid snapshot: ' + field)
        except Exception:
            record['errors'].append(traceback.format_exc())
        record['source_hashes_after'] = check_sources(root, plan['source_hashes'])
        record['finished_utc'] = now()
        record['passed'] = not record['errors']
        record['stdout_sha256'], record['stderr_sha256'] = sha(stdout), sha(stderr)
        if sha(wrapper_path) != wrapper_hash:
            raise RuntimeError('Wrapper changed during execution')
        write(folder / 'execution.json', record)
        report['jobs'].append(record)
        write(destination / 'retention_execution.json', report)
        print(json.dumps({'completed': ordinal + 1, 'total': 30,
                          'job_id': job['job_id'], 'passed': record['passed']}), flush=True)
    report['finished_utc'] = now()
    report['all_passed'] = all(j['passed'] for j in report['jobs'])
    report['status'] = 'passed' if report['all_passed'] else 'failed'
    write(destination / 'retention_execution.json', report)
    if not report['all_passed']:
        raise SystemExit('Retention failures preserved; inspect raw logs')


if __name__ == '__main__':
    main()
