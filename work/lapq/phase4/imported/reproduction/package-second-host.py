#!/usr/bin/env python3
"""Package completed second-host evidence and verify ZIP CRC/member hashes."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path('/root/research/learned-pq-second-host')
WORKSPACE = Path('/mnt/c/Users/somam/Documents/Codex/2026-11-06/read-windows-second-machine-handoff-txt')
EVIDENCE = ROOT/'second_host_evidence'
DEST = WORKSPACE/'outputs'
archive_path = DEST/'windows-wsl2-second-host-results.zip'
assert not archive_path.exists(), 'Refuse to overwrite an existing results archive'
audit = json.loads((EVIDENCE/'comparison/comparison_audit.json').read_text())
assert audit['status'] == 'passed'
retention = json.loads((ROOT/'work/lapq/phase3/second_host_retention/retention_execution.json').read_text())
assert retention['all_passed'] and len(retention['jobs']) == 30
native = json.loads((ROOT/'work/lapq/phase2/second_host_native/native_summary.json').read_text())
assert native['coverage']['complete_and_matching'] and native['raw_native_timing_records_seen'] == 3444

readme = '''WINDOWS + WSL2 SECOND PHYSICAL HOST RESULTS

Measurements completed on the user-identified second laptop, Dell Latitude 5400,
Intel Core i5-8365U, 8 GiB installed RAM, Windows 11 Pro 25H2 build 26200.9168.
Linux execution: Ubuntu-24.04 under WSL2 6.18.54.1-microsoft-standard-WSL2,
Python 3.12.3, g++ 13.3.0, C++17 -O3 -DNDEBUG, no fast-math.
42 cases / 3,444 native timings and 30 retention workers all passed.

Read RESULTS-SUMMARY.txt first. comparison/ has independently audited paired
ratios (both heap denominators), changed descriptive rankings, and retention
snapshots side by side with the Mac. hardware_and_logs/ has real Windows/WSL
metadata, build/dependency/fetch/selfcheck logs, power and clock observations.
second_host_native/ has new native records, CSVs, logs, validation distances,
build metadata and binaries. second_host_retention/ has unchanged specs, raw
worker output, parsed records, execution guards and the frozen wrapper.
reference_first_host/ contains unchanged baseline native and retention records.
original_study/ holds the unchanged transferred study ZIP and its sidecar.
reproduction/ has new orchestration scripts and the exact native source.

Original first-host sources, plans, results, preflight/gate files and retention
results were preserved. The manifest preservation audit verified 479 outer and
1,315 nested base members after measurement. All first-host files in this ZIP
are references, not newly measured Windows evidence. The shipped phase-three
gate was not rerun or claimed as independent second-host validation. Every new
retention worker verifies its actual extractions and frozen source hashes.

Raw records retain original absolute execution paths as provenance. Native CSVs
are beside their cases; native_summary.py supports relocated sibling CSVs.
Generated graph exports and oracle input files, downloaded upstream sources/data,
and virtual environments are omitted. Their hashes are in native_protocol.json
and the original input manifests. Regenerate these through the original pinned
fetcher/protocol if needed. Original data/source downloads are not redistributed.
For reruns, extract the original study and its nested base-study-artifact.zip to
the same fresh Linux root, install pinned requirements, fetch and hash-check
inputs, and use fresh result destinations. Run all stages sequentially.

The default Ubuntu installer provided Ubuntu 26.04/Python 3.14; no measurements
ran there. Ubuntu-24.04 was installed for Python 3.12 compatibility with the
unchanged dependencies. Experiments ran explicitly as WSL root without creating
an interactive Linux password/account. Ubuntu 26.04 was left installed.

Observed Windows and WSL calendar clocks differed by about 45 s in a post-run
handshake. Synchronous orchestration established stage order; runtime durations
used monotonic/process clocks. Hardware, OS, compiler, library and Python-version
changes are confounded across hosts; compare paired ratios and ownership trends.
Frequency, thermals and background OS work were uncontrolled. This is Windows
+ WSL2, not bare-metal Linux. CPU/RAM and power observations are in the metadata.

archive-manifest.json covers every member except itself. The external sidecar
contains the whole-ZIP SHA256, member count and CRC/member verification result.
All reported success comes from actual completed runs and audited raw records.
Return to the Mac with this result ZIP; the Windows session is complete.
'''
(EVIDENCE/'README.txt').write_text(readme)

members = {}


def add(source, name):
    source = Path(source)
    assert source.is_file(), source
    assert name not in members, name
    members[name] = source


def add_tree(source, prefix, excluded=()):
    for source_file in sorted(Path(source).rglob('*')):
        if source_file.is_file():
            relative = source_file.relative_to(source)
            if not any(part in excluded for part in relative.parts):
                add(source_file, str(Path(prefix)/relative))


add(EVIDENCE/'RESULTS-SUMMARY.txt', 'RESULTS-SUMMARY.txt')
add(EVIDENCE/'README.txt', 'README.txt')
for item in EVIDENCE.iterdir():
    if item.name in ('RESULTS-SUMMARY.txt', 'README.txt', 'comparison') or item.name == '__pycache__':
        continue
    if item.is_dir():
        add_tree(item, 'hardware_and_logs/'+item.name, ('__pycache__',))
    else:
        add(item, 'hardware_and_logs/'+item.name)
add_tree(EVIDENCE/'comparison', 'comparison')
add_tree(ROOT/'work/lapq/phase2/second_host_native', 'second_host_native', ('inputs', 'oracles', '__pycache__'))
add_tree(ROOT/'work/lapq/phase3/second_host_retention', 'second_host_retention', ('__pycache__',))
add_tree(ROOT/'work/lapq/phase2/native_results', 'reference_first_host/native_results')
for job in json.loads((EVIDENCE/'second-host-retention-plan.json').read_text(encoding='utf-8-sig'))['jobs']:
    add(ROOT/'work/lapq/phase3/results'/job['output_name'], 'reference_first_host/retention/'+job['output_name'])
for filename in ('learned-priority-queues-working-paper-and-ablations.zip',
                 'learned-priority-queues-working-paper-and-ablations.manifest.json',
                 'windows-second-machine-handoff.txt', 'transfer-manifest.json'):
    add(WORKSPACE/'work/transfer-original'/filename, 'original_study/'+filename)
for source_file in ('native_benchmark.cpp', 'native_experiment.py', 'native_summary.py', 'workload_manifest.json'):
    add(ROOT/'work/lapq/phase2'/source_file, 'reproduction/phase2/'+source_file)
for filename in ('compare-second-host.py', 'package-second-host.py', 'prepare-linux-study.py',
                 'bootstrap-second-host.sh', 'run-native-stages.sh', 'run-native-awake.ps1',
                 'run-retention-stages.sh', 'capture-windows-host.ps1', 'linux-host-metadata.py',
                 'second_host_retention.py', 'second-host-retention-plan.json',
                 'transfer-verification.json', 'study-verification.json',
                 'wsl-setup-events.json', 'wsl-setup-reboot-required.json'):
    add(WORKSPACE/'work'/filename, 'reproduction/'+filename)
manifest = {'created_utc': datetime.now(timezone.utc).isoformat(),
            'status': 'All measurements complete and audited', 'native_cases': 42,
            'native_timings': 3444, 'retention_workers': 30,
            'hash_scope': 'Every ZIP member except archive-manifest.json itself',
            'files': {name: {'bytes': path.stat().st_size,
                              'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                      for name, path in sorted(members.items())}}
with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for name, path in sorted(members.items()):
        z.write(path, name)
    z.writestr('archive-manifest.json', json.dumps(manifest, indent=2)+'\n')
with zipfile.ZipFile(archive_path) as z:
    assert z.testzip() is None
    assert len(z.infolist()) == len(members)+1
    for name, entry in manifest['files'].items():
        data = z.read(name)
        assert len(data) == entry['bytes']
        assert hashlib.sha256(data).hexdigest() == entry['sha256'], name
digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
sidecar = {'archive': archive_path.name, 'sha256': digest,
           'bytes': archive_path.stat().st_size, 'members': len(members)+1,
           'crc_and_member_sha256_verification': 'passed',
           'measurements_complete': True, 'native_cases': 42,
           'native_timings': 3444, 'retention_workers': 30}
(DEST/'windows-wsl2-second-host-results.manifest.json').write_text(json.dumps(sidecar, indent=2)+'\n')
shutil.copyfile(EVIDENCE/'RESULTS-SUMMARY.txt', DEST/'windows-wsl2-second-host-summary.txt')
print(json.dumps(sidecar))
