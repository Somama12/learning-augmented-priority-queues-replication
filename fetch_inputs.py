#!/usr/bin/env python3
"""Fetch separately distributed public inputs, verifying pinned content hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent

def sha256(data):
    return hashlib.sha256(data).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--replace-existing', action='store_true')
    args = parser.parse_args()
    manifest = json.loads((ROOT / 'input_manifest.json').read_text())
    for item in manifest:
        target = (ROOT / item['target']).resolve()
        if ROOT not in target.parents:
            raise RuntimeError('Input target escapes archive root')
        if target.exists():
            if sha256(target.read_bytes()) == item['local_sha256']:
                print('Verified:', item['target'])
                continue
            if args.check_only or not args.replace_existing:
                raise RuntimeError('Existing input differs from pinned hash: ' + item['target'])
        elif args.check_only:
            raise RuntimeError('Missing input: ' + item['target'])
        request = urllib.request.Request(item['url'], headers={
            'User-Agent': 'LAPQ-expanded-study-reproducer/1.0'})
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
        if sha256(data) != item['download_sha256']:
            raise RuntimeError('Download differs from pinned hash: ' + item['target'])
        normalization = item.get('normalization', 'none')
        if normalization == 'append_one_newline':
            data += b'\n'
        elif normalization != 'none':
            raise RuntimeError('Unknown input normalization')
        if sha256(data) != item['local_sha256']:
            raise RuntimeError('Normalized input differs from pinned hash')
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + '.download.tmp')
        temporary.write_bytes(data)
        temporary.replace(target)
        print('Fetched and verified:', item['target'])

if __name__ == '__main__':
    main()
