#!/usr/bin/env python3
"""Verify both immutable archive layers and extract into a new Linux directory."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


archive, target = map(Path, sys.argv[1:])
expected = 'f0c5a7bfbfb3e82f1dac8d89b1a2105475c7161f486fd71ef649a2a7baa0baa8'
if sha(archive) != expected:
    raise SystemExit('Study archive SHA256 mismatch')
target.mkdir(parents=True, exist_ok=False)


def extract(path):
    with zipfile.ZipFile(path) as z:
        if z.testzip() is not None:
            raise RuntimeError('ZIP CRC verification failed')
        for entry in z.infolist():
            destination = (target / entry.filename).resolve()
            if target.resolve() not in destination.parents:
                raise RuntimeError('Unsafe archive member path')
            if entry.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                data = z.read(entry)
                if destination.exists() and destination.read_bytes() != data:
                    raise RuntimeError('Conflicting archive overlap: ' + entry.filename)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.exists():
                    destination.write_bytes(data)


extract(archive)
outer = json.loads((target / 'completed-study-manifest.json').read_text())
if sha(target / 'base-study-artifact.zip') != outer['base_archive_sha256']:
    raise SystemExit('Nested base archive SHA256 mismatch')
extract(target / 'base-study-artifact.zip')
base = json.loads((target / 'artifact_checksums.json').read_text())
for layer in (outer['files'], base):
    for name, checksum in layer.items():
        if sha(target / name) != checksum:
            raise SystemExit('Manifest mismatch: ' + name)
report = {'status': 'passed', 'archive_sha256': expected,
          'base_archive_sha256': outer['base_archive_sha256'],
          'outer_members_verified': len(outer['files']),
          'base_members_verified': len(base), 'measurements_started': False}
(target / 'second_host_evidence/logs').mkdir(parents=True)
(target / 'second_host_evidence/archive_verification.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(report))
