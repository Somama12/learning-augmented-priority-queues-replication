#!/usr/bin/env python3
"""Read-only verification of a restored tree against verified layered evidence.

Example: python3 work/lapq/phase6/verify_current_study.py --root reconstructed-study
  --archive outputs/learned-priority-queues-two-host-artifact.zip
No benchmark, fetched source, or imported second-host program is executed.
"""
import argparse
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def coordinator_restorer():
    path = HERE.parent / 'phase4/restore_two_host_study.py'
    spec = importlib.util.spec_from_file_location('coordinator_restorer', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify(root, archive=None, bundle=None):
    root = Path(root).resolve()
    restorer = coordinator_restorer()
    members, manifest, returned, original, base, overlay = restorer.load_bundle(
        archive_path=archive, directory=bundle)
    expected = dict(base)
    expected.update(original)
    expected.update({'work/lapq/phase4/imported/' + n:d for n,d in returned.items()})
    expected.update({
        'work/lapq/phase4/' + restorer.RETURNED: members[restorer.RETURNED],
        'outputs/' + restorer.ORIGINAL: returned['original_study/' + restorer.ORIGINAL],
        'README-TWO-HOST.txt': members['README-TWO-HOST.txt'],
        restorer.MANIFEST: members[restorer.MANIFEST],
        'restore_two_host_study.py': members['restore_two_host_study.py'],
    })
    expected.update(overlay)
    errors = []
    for name, data in expected.items():
        path = root / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            errors.append({'file': name, 'reason': 'missing, symlink, or escaping file'})
        elif any(p.is_symlink() for p in path.parents if p != root.parent):
            errors.append({'file': name, 'reason': 'symlink ancestor'})
        elif restorer.digest(path.read_bytes()) != restorer.digest(data):
            errors.append({'file': name, 'reason': 'SHA256 differs from final layer'})
    receipt_path = root / 'restoration-receipt.json'
    if receipt_path.is_file() and not receipt_path.is_symlink():
        receipt = json.loads(receipt_path.read_text())
        if receipt.get('status') != 'passed' or receipt.get('outer_manifest_sha256') != restorer.digest(members[restorer.MANIFEST]):
            errors.append({'file': 'restoration-receipt.json', 'reason': 'wrong restoration status or manifest binding'})
    else:
        errors.append({'file': 'restoration-receipt.json', 'reason': 'missing or symlink receipt'})
    return {'status': 'passed' if not errors else 'failed', 'expected_final_files': len(expected),
            'outer_manifest_sha256': restorer.digest(members[restorer.MANIFEST]),
            'errors': errors, 'layer_precedence': ['base', 'first_host', 'returned_prefix', 'copies', 'overlay'],
            'scope': 'All final files required by this verified bundle; extra fetched inputs, dependencies, caches, or new analysis outputs are not rejected. Historical base checksums remain unchanged.',
            'imported_code_executed': False, 'benchmarks_executed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--archive', type=Path)
    source.add_argument('--bundle', type=Path)
    args = parser.parse_args()
    result = verify(args.root, args.archive, args.bundle)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['status'] == 'passed' else 1)


if __name__ == '__main__':
    main()
