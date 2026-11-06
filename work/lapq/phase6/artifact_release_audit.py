#!/usr/bin/env python3
"""Read-only inventory/checksum/disclosure audit. Does not extract or execute code."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PATH = ROOT / 'outputs/learned-priority-queues-two-host-artifact.zip'
TEXT_EXTENSIONS = {'.py', '.cpp', '.h', '.json', '.txt', '.md', '.csv', '.tex', '.sh', '.ps1', '.yml', '.yaml'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_zip(data, label):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        unsafe = [n for n in names if PurePosixPath(n).is_absolute() or '..' in PurePosixPath(n).parts or '\\' in n]
        symlinks = [i.filename for i in infos if stat.S_ISLNK(i.external_attr >> 16)]
        files = {i.filename: archive.read(i) for i in infos if not i.is_dir()}
        info = {'label': label, 'sha256': sha(data), 'archive_bytes': len(data),
                'members': len(names), 'uncompressed_bytes': sum(i.file_size for i in infos),
                'crc_error': archive.testzip(), 'duplicate_names': len(names) != len(set(names)),
                'unsafe_paths': unsafe, 'symlinks': symlinks,
                'pdfs': [n for n in names if n.endswith('.pdf')],
                'license_notice_files': [n for n in names if re.search(r'(^|/)(licen[sc]e|notice|copying|authors|citation)(\.|$)', n, re.I)]}
        return files, info


def verify(files, mapping, excluded):
    bad = []
    if set(files) - set(excluded) != set(mapping):
        bad.append('member coverage differs')
    for name, expected in mapping.items():
        wanted = expected if isinstance(expected, str) else expected['sha256']
        if name not in files or sha(files[name]) != wanted:
            bad.append(name)
        elif isinstance(expected, dict) and 'bytes' in expected and len(files[name]) != expected['bytes']:
            bad.append('size: ' + name)
    return bad


def main():
    outer_bytes = PATH.read_bytes()
    outer, oi = read_zip(outer_bytes, 'outer')
    returned, ri = read_zip(outer['windows-wsl2-second-host-results.zip'], 'returned')
    first_name = 'original_study/learned-priority-queues-working-paper-and-ablations.zip'
    original, fi = read_zip(returned[first_name], 'first_host')
    base, bi = read_zip(original['base-study-artifact.zip'], 'base')
    manifest = json.loads(outer['two-host-manifest.json'])
    first_manifest = json.loads(original['completed-study-manifest.json'])
    checksums = json.loads(base['artifact_checksums.json'])
    checks = {'outer': verify(outer, manifest['files'], ['two-host-manifest.json']),
              'returned_against_outer_manifest': verify(returned, manifest['returned_zip_members'], []),
              'first_host': verify(original, first_manifest['files'], ['completed-study-manifest.json']),
              'base': verify(base, checksums, ['artifact_checksums.json'])}
    restored = dict(base)
    restored.update(original)
    restored.update({n:d for n,d in outer.items() if n.startswith(('work/', 'outputs/'))})
    old_checker_mismatches = [n for n,h in checksums.items() if n in restored and sha(restored[n]) != h]
    identity_patterns = {
        'absolute_macos_home_path': re.compile(r'/Users/[^/\s"\\]+/'),
        'absolute_windows_home_path': re.compile(r'(?:C:[\\/]+Users[\\/]+|/mnt/c/Users/)[^/\s"\\]+'),
        'known_author_or_account': re.compile(r'Somama Siddiqui|somamasiddiqui|Somama12', re.I),
        'computer_name_field': re.compile(r'"computer_name"\s*:'),
        'upstream_issue_link': re.compile(r'Learning-augmented-priority-queues/issues/1')}
    secret_patterns = {
        'aws_access_id': re.compile(r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b'),
        'github_token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b'),
        'openai_token_shape': re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{40,}\b'),
        'private_key_header': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')}
    disclosure = {k:[] for k in identity_patterns}
    secrets = {k:[] for k in secret_patterns}
    text_count = 0
    for label, files in [('outer',outer),('returned',returned),('first_host',original),('base',base)]:
        for name, data in files.items():
            if Path(name).suffix.lower() not in TEXT_EXTENSIONS:
                continue
            try:
                text = data.decode('utf-8-sig')
            except UnicodeDecodeError:
                continue
            text_count += 1
            for kind, pattern in identity_patterns.items():
                if pattern.search(text): disclosure[kind].append(label + ':' + name)
            for kind, pattern in secret_patterns.items():
                if pattern.search(text): secrets[kind].append(label + ':' + name)
    tex = outer['outputs/learned-priority-queues-manuscript-draft.tex'].decode()
    figures = re.findall(r'\\includegraphics(?:\[[^]]*\])?\{([^}]+)\}', tex)
    requirements = {n:base[n].decode().splitlines() for n in ['requirements.txt','requirements-report.txt']}
    output = {'archive': str(PATH.relative_to(ROOT)), 'archive_sha256': sha(outer_bytes),
              'archive_size_bytes': len(outer_bytes), 'layers': [oi,ri,fi,bi],
              'checksum_errors': checks, 'checksums_passed': not any(checks.values()),
              'base_checker_mismatches_after_current_overlay': old_checker_mismatches,
              'requirements': requirements, 'tex_figure_names': figures,
              'missing_tex_figures': [n for n in figures if 'outputs/' + n not in outer],
              'text_members_scanned': text_count,
              'disclosure_counts': {k:len(v) for k,v in disclosure.items()},
              'disclosure_example_files': {k:v[:8] for k,v in disclosure.items()},
              'credential_pattern_hit_files': secrets,
              'credential_scan_limit': 'Bounded pattern scan of UTF-8 text only, plus manual screenshot review; not a guarantee that every secret is absent. Binary executables and PDF streams were not scanned as text.',
              'coverage': json.loads(outer['work/lapq/phase4/validated_second_host.json'])['coverage'],
              'dependency_bundles_present': [label+':'+n for label,files in [('outer',outer),('returned',returned),('first_host',original),('base',base)] for n in files if '/site-packages/' in n or '/deps/' in n],
              'source_script_sha256': sha(Path(__file__).read_bytes())}
    (HERE / 'artifact_release_inventory.json').write_text(json.dumps(output,indent=2)+'\n')
    print(json.dumps({k:output[k] for k in ['archive_sha256','archive_size_bytes','checksums_passed','base_checker_mismatches_after_current_overlay','disclosure_counts','credential_pattern_hit_files','missing_tex_figures']}))


if __name__ == '__main__':
    main()
