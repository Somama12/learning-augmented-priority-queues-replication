#!/usr/bin/env python3
"""Verify and restore the two-host study without executing any bundled code.

After unzipping the outer artifact:
  python3 restore_two_host_study.py --destination reconstructed-study
Or place this script beside the artifact ZIP and supply --archive explicitly.
The destination must not exist. Only standard-library modules are required.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import zipfile

FORMAT = 'learned-priority-queues-two-host-v1'
MANIFEST = 'two-host-manifest.json'
RETURNED = 'windows-wsl2-second-host-results.zip'
ORIGINAL = 'learned-priority-queues-working-paper-and-ablations.zip'
EXPECTED_RETURNED = 'dc206a541d0e2797925b532d609a01ed76fb5c794b383287c5805b9dabf07083'
EXPECTED_ORIGINAL = 'f0c5a7bfbfb3e82f1dac8d89b1a2105475c7161f486fd71ef649a2a7baa0baa8'
LIMIT = 1024**3


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def safe_name(name):
    path = PurePosixPath(name)
    return (bool(name) and not path.is_absolute() and name == path.as_posix()
            and '..' not in path.parts and '\\' not in name
            and not any(':' in part for part in path.parts))


def zip_members(data, label):
    """Read only regular, canonical relative members; never call extractall."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        require(len(names) == len(set(names)), label + ': duplicate ZIP names')
        require(sum(item.file_size for item in infos) < LIMIT, label + ': expanded-size limit')
        for item in infos:
            require(safe_name(item.filename.rstrip('/')), label + ': unsafe member path')
            mode = item.external_attr >> 16
            require(not stat.S_ISLNK(mode), label + ': symlink member')
            require(not (mode & 0o170000) or stat.S_ISREG(mode) or stat.S_ISDIR(mode),
                    label + ': special-file member')
            require(not item.flag_bits & 1, label + ': encrypted member')
        require(archive.testzip() is None, label + ': bad CRC')
        members = {item.filename: archive.read(item) for item in infos if not item.is_dir()}
    # Reject file-as-parent conflicts, including case-insensitive Windows collisions.
    lower = {name.casefold() for name in members}
    require(len(lower) == len(members), label + ': case-insensitive filename collision')
    for name in members:
        for parent in PurePosixPath(name).parents:
            require(parent.as_posix().casefold() not in lower, label + ': file is also a directory')
    return members


def verify_map(members, expected, label, excluded=()):
    require(set(members) - set(excluded) == set(expected), label + ': checksum coverage mismatch')
    for name, entry in expected.items():
        require(safe_name(name), label + ': unsafe manifest path')
        data = members[name]
        wanted = entry if isinstance(entry, str) else entry['sha256']
        require(digest(data) == wanted, label + ': SHA mismatch: ' + name)
        if isinstance(entry, dict):
            require(len(data) == entry['bytes'], label + ': size mismatch: ' + name)


def load_bundle(archive_path=None, directory=None):
    if archive_path is not None:
        members = zip_members(Path(archive_path).read_bytes(), 'outer artifact')
    else:
        directory = Path(directory).resolve()
        manifest_path = directory / MANIFEST
        require(manifest_path.is_file() and not manifest_path.is_symlink(), 'missing outer manifest')
        manifest = json.loads(manifest_path.read_bytes())
        members = {MANIFEST: manifest_path.read_bytes()}
        for name in manifest['files']:
            require(safe_name(name), 'unsafe outer member path')
            path = directory / name
            require(path.is_file() and not path.is_symlink(), 'missing/symlink outer member: ' + name)
            require(path.resolve().is_relative_to(directory), 'outer member escapes bundle')
            require(not any(parent.is_symlink() for parent in path.parents if parent != directory.parent),
                    'symlink ancestor in outer bundle')
            members[name] = path.read_bytes()
    manifest = json.loads(members[MANIFEST])
    require(manifest['format'] == FORMAT, 'unsupported artifact format')
    verify_map(members, manifest['files'], 'outer artifact', [MANIFEST])
    returned_data = members[RETURNED]
    require(digest(returned_data) == EXPECTED_RETURNED == manifest['returned_zip_sha256'],
            'returned ZIP identity differs')
    returned = zip_members(returned_data, 'returned evidence')
    verify_map(returned, manifest['returned_zip_members'], 'returned evidence')
    original_data = returned['original_study/' + ORIGINAL]
    require(digest(original_data) == EXPECTED_ORIGINAL == manifest['first_host_zip_sha256'],
            'original first-host ZIP identity differs')
    original = zip_members(original_data, 'first-host study')
    first_manifest = json.loads(original['completed-study-manifest.json'])
    verify_map(original, first_manifest['files'], 'first-host study', ['completed-study-manifest.json'])
    base_data = original['base-study-artifact.zip']
    require(digest(base_data) == first_manifest['base_archive_sha256'] == manifest['base_zip_sha256'],
            'base study ZIP identity differs')
    base = zip_members(base_data, 'base study')
    verify_map(base, json.loads(base['artifact_checksums.json']), 'base study', ['artifact_checksums.json'])
    overlay = {name: data for name, data in members.items() if name.startswith(('work/', 'outputs/'))}
    require(sorted(overlay) == manifest['overlay_members'], 'overlay member list differs')
    require('work/lapq/phase4/imported/' not in ''.join(overlay), 'returned evidence duplicated in overlay')
    supplement = manifest['first_host_distance_supplement']['files']
    first_native = json.loads(base['work/lapq/phase2/native_results/native_experiment.json'])
    first_cases = {case['case']['case_id']: case for case in first_native['cases']}
    require(len(supplement) == len(first_cases) == 42, 'first-host distance supplement coverage differs')
    require({entry['case_id'] for entry in supplement.values()} == set(first_cases),
            'first-host distance supplement case IDs differ')
    for name, entry in supplement.items():
        case_id = entry['case_id']
        case = first_cases[case_id]
        require(name == 'work/lapq/phase2/native_results/cases/' + case_id + '/distances.f64le',
                'invalid first-host distance supplement path')
        verification = case['distance_verification']
        require(digest(overlay[name]) == entry['sha256'] == verification['actual_sha256'] ==
                verification['expected_sha256'] == case['case']['oracle_binary64_sha256'],
                'first-host distance supplement oracle hash differs')
        require(len(overlay[name]) == entry['bytes'] == verification['actual_bytes'] ==
                verification['expected_bytes'] == case['case']['oracle_binary64_bytes'],
                'first-host distance supplement byte count differs')
    return members, manifest, returned, original, base, overlay


def write_member(root, name, data):
    require(safe_name(name), 'unsafe restore path')
    path = root / name
    require(path.resolve().is_relative_to(root), 'restore path escapes destination')
    path.parent.mkdir(parents=True, exist_ok=True)
    require(not path.is_symlink(), 'restore target is symlink')
    path.write_bytes(data)
    # Executable bits from foreign binaries/scripts are deliberately not restored.
    path.chmod(0o644)


def restore(destination, archive_path=None, directory=None):
    # Full validation precedes the first filesystem write.
    layers = load_bundle(archive_path, directory)
    members, manifest, returned, original, base, overlay = layers
    destination = Path(os.path.abspath(destination))
    require(not destination.exists() and not destination.is_symlink(), 'destination already exists')
    require(destination.parent.is_dir(), 'destination parent must exist')
    require(destination.parent.resolve() == destination.parent, 'destination parent contains symlinks')
    destination.mkdir(exist_ok=False)
    expected = {}
    for layer in (base, original):
        for name, data in layer.items():
            write_member(destination, name, data)
            expected[name] = digest(data)
    for name, data in returned.items():
        target = 'work/lapq/phase4/imported/' + name
        write_member(destination, target, data)
        expected[target] = digest(data)
    copies = {
        'work/lapq/phase4/' + RETURNED: members[RETURNED],
        'outputs/' + ORIGINAL: returned['original_study/' + ORIGINAL],
        'README-TWO-HOST.txt': members['README-TWO-HOST.txt'],
        'two-host-manifest.json': members[MANIFEST],
        'restore_two_host_study.py': members['restore_two_host_study.py'],
    }
    for layer in (copies, overlay):
        for name, data in layer.items():
            write_member(destination, name, data)
            expected[name] = digest(data)
    for name, wanted in expected.items():
        require(digest((destination / name).read_bytes()) == wanted, 'restored checksum mismatch: ' + name)
    receipt = {
        'status': 'passed', 'operation': 'verified extraction only; no bundled code executed',
        'outer_manifest_sha256': digest(members[MANIFEST]),
        'returned_zip_sha256': manifest['returned_zip_sha256'],
        'first_host_zip_sha256': manifest['first_host_zip_sha256'],
        'base_zip_sha256': manifest['base_zip_sha256'],
        'verified_final_files': len(expected), 'base_members': len(base),
        'first_host_members': len(original), 'returned_members': len(returned),
        'overlay_members': len(overlay),
        'original_mac_distance_supplement_files': len(manifest['first_host_distance_supplement']['files']),
        'dataset_note': 'Third-party upstream source and graph data are intentionally absent; use the restored fetch instructions before checks requiring those pins.',
    }
    write_member(destination, 'restoration-receipt.json', (json.dumps(receipt, indent=2) + '\n').encode())
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--archive', type=Path, help='Optional outer ZIP; otherwise use files beside this script')
    args = parser.parse_args()
    print(json.dumps(restore(args.destination, args.archive, Path(__file__).resolve().parent), indent=2))


if __name__ == '__main__':
    main()
