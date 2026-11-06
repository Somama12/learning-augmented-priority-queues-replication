#!/usr/bin/env python3
"""Fetch separately distributed references; never execute downloaded source."""
import argparse,hashlib,json
from pathlib import Path
import urllib.request
here=Path(__file__).resolve().parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--check-only',action='store_true')
parser.add_argument('--include-oracle',action='store_true',help='Also fetch four original Python oracle files for optional differential checks')
args=parser.parse_args()
manifest=json.loads((here/'reference_fetch_manifest.json').read_text())
for item in manifest:
 if item['kind']=='oracle' and not args.include_oracle:continue
 target=(here/item['target']).resolve()
 if here.parent not in target.parents:raise SystemExit('Unsafe reference target')
 if target.exists():
  if hashlib.sha256(target.read_bytes()).hexdigest()!=item['local_sha256']:
   raise SystemExit('Existing reference differs; preserve it and resolve discrepancy: '+str(target))
  print('Verified:',item['target']);continue
 if args.check_only:raise SystemExit('Missing reference: '+item['target'])
 request=urllib.request.Request(item['url'],headers={'User-Agent':'LAPQ-partial-replication-reference/1.0'})
 with urllib.request.urlopen(request,timeout=120) as response:data=response.read()
 if hashlib.sha256(data).hexdigest()!=item['download_sha256']:raise SystemExit('Download hash differs: '+item['target'])
 if item.get('git_blob_sha1'):
  blob=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
  if blob!=item['git_blob_sha1']:raise SystemExit('Git blob hash differs: '+item['target'])
 if item['normalization']=='append_one_newline':data+=b'\n'
 elif item['normalization']!='none':raise SystemExit('Unknown normalization')
 if hashlib.sha256(data).hexdigest()!=item['local_sha256']:raise SystemExit('Normalized hash differs: '+item['target'])
 target.parent.mkdir(parents=True,exist_ok=True)
 temporary=target.with_suffix(target.suffix+'.download.tmp');temporary.write_bytes(data);temporary.replace(target)
 print('Fetched and verified:',item['target'])
