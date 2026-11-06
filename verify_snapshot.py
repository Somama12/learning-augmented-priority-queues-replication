#!/usr/bin/env python3
"""Verify snapshot-owned files; downloaded inputs and analysis outputs are extra."""
import hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parent
manifest=json.loads((root/'snapshot-manifest.json').read_text())
errors=[]
for name,item in manifest['files'].items():
 p=root/name
 if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(root):
  errors.append(name+': missing, symlink or escaping path')
 elif hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']:
  errors.append(name+': SHA256 mismatch')
print(json.dumps({'status':'failed' if errors else 'passed','verified_files':len(manifest['files']),'errors':errors},indent=2))
raise SystemExit(bool(errors))
