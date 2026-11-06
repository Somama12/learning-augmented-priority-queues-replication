#!/usr/bin/env python3
"""Curate a named, source/results repository from verified historical layers.

No benchmarks run. --validate reanalyzes numerical evidence in a disposable copy.
The authoring workspace and historical archives are never modified.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DEST = ROOT / 'outputs/learned-priority-queue-audit-repository'
ARCHIVE = ROOT / 'outputs/learned-priority-queues-two-host-artifact.zip'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def restorer():
    spec = importlib.util.spec_from_file_location('local_restorer', HERE.parent/'phase4/restore_two_host_study.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


P3 = {'ablation_analysis.py','ablation_protocol.json','ablation_queues.py','ablation_runner.py',
      'ablation_summary.json','execution_manifest.json','freeze_proposal.py','full_trace_gate.py',
      'full_trace_gate_results.json','preflight.py','preflight_results.json','runner_selfcheck.json',
      'minimal_detachment_failure.json','unsafe_detachment_failure.json','unsafe_detachment_initial.py',
      'independent_ratio_audit.json','plot_ablation_memory.py'}
P4 = {'native_cross_host_analysis.py','retention_cross_host_analysis.py','native_cross_host_summary.json',
      'retention_cross_host_summary.json','plot_cross_host.py','restore_two_host_study.py'}
P5 = {'build_retention_figure.py','issue_reproducer.py','issue_reproducer_evidence.json'}


def retain(name):
    p = Path(name)
    if any(part in {'__pycache__','mplconfig','mpl-cache','inputs','oracles','render','deps','upstream','data'} for part in p.parts):
        return False
    if p.suffix not in {'.py','.cpp','.json','.csv','.txt','.log','.sha256','.f64le','.sh','.ps1'}:
        return False
    if name.startswith('work/lapq/phase2/'):
        return p.name not in {'REPRODUCIBILITY.txt','package_expanded.py','build_expanded_report.py','native_report_section.json','render.log','render-final.log'}
    if name.startswith('work/lapq/phase3/'):
        return p.name in P3 or any('/'+part+'/' in name for part in ('results','trace_gate_results','tables'))
    if name.startswith('work/lapq/phase4/'):
        return p.name in P4 or '/tables/' in name
    if name.startswith('work/lapq/phase5/'):
        return p.name in P5
    if name.startswith('work/lapq/') and len(p.parts)==3:
        return p.name not in {'build_report.py','package_study.py'}
    return False


VERIFY = '''#!/usr/bin/env python3
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
'''

LICENSE = '''MIT License

Copyright (c) 2026 Somama Siddiqui

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
'''

README = '''# Learning-augmented priority queue audit

Source, frozen experiment plans, raw observations, and analysis for an empirical
audit of the released Benomar–Coester learning-augmented priority queue artifact.
Author: **Somama Siddiqui**. Codex was used as a research and coding tool.

The study investigates historical queue metadata, controlled cleanup/bucket
ablations, and practical costs under the declared advice model. It does not
refute the original comparison bounds or claim a new asymptotic queue algorithm.
Two-host native and bounded-live retention results are included; Python graph
timings and graph-memory ablations remain first-host measurements. All graph
timings across stages/hosts total 12,300. Read the raw protocols for exact scope.

## Quickstart: verification and analysis, no benchmarks

Use Python 3.12 or 3.13 from this repository root. Snapshot verification and native
reanalysis use only the standard library:

```sh
python3 verify_snapshot.py
python3 work/lapq/phase4/native_cross_host_analysis.py --output reanalysis/native.json --tables reanalysis/native-tables
```

The second command audits both hosts' 42 cases, all distance bytes, paired timing
records, and reported comparisons before recomputing the summaries. It executes
no imported program. Its bootstrap intervals are exploratory for the fixed inputs.

Retention auditing also checks pinned external source bytes. Fetch the separately
distributed inputs first; the downloader verifies every SHA256 and fails on any
substitution. Review `THIRD_PARTY_NOTICES.md` before using external code/data:

```sh
python3 fetch_inputs.py
python3 fetch_inputs.py --check-only
python3 work/lapq/phase4/retention_cross_host_analysis.py
```

Retention analysis writes its shipped summary path. Run this last command in a
disposable copy if preserving the snapshot byte-for-byte. It audits all 30 paired
retention cells, source/spec guards, hardware observations, and the documented
calendar anomaly. Both analyses were checked in a disposable repository copy;
validation evidence is available in `provenance/repository-validation.json`.

## Reproducing experiments

Experiments are optional and can take appreciably longer than reanalysis. Do not
run compilation, plotting, tracing, or another benchmark concurrently with timing.
Use fresh output directories and keep failures. Core dependencies are pinned:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python fetch_inputs.py
.venv/bin/python work/lapq/phase2/test_indexes.py
.venv/bin/python work/lapq/phase2/test_veb_radix.py
.venv/bin/python work/lapq/phase2/benchmark.py selfcheck
```

Native reproduction requires a C++17 compiler. The preparation stage compiles,
exports frozen graphs, runs correctness checks, and optionally runs sanitizers:

```sh
.venv/bin/python work/lapq/phase2/native_experiment.py --stage plan --destination work/lapq/phase2/rerun_native
.venv/bin/python work/lapq/phase2/native_experiment.py --stage prepare --destination work/lapq/phase2/rerun_native --compiler g++ --sanitizers
.venv/bin/python work/lapq/phase2/native_experiment.py --stage run --destination work/lapq/phase2/rerun_native --timeout 180
.venv/bin/python work/lapq/phase2/native_summary.py --input work/lapq/phase2/rerun_native/native_experiment.json --output work/lapq/phase2/rerun_native/native_summary.json
```

For Python, the existing phase-two plans and phase-three manifest prescribe the
original jobs. Consult each runner's `--help` and the frozen protocols; never
overwrite shipped results or regenerate their plans merely to bypass a guard.
`--approved-run` is a local execution guard, not an external scientific approval.
Use a separate disposable copy for checks that write pinned prerequisite records.

## Layout and provenance

- `work/lapq/phase2`: queue/index implementations, workloads, native code, all
  first-host raw result stages, manifests, and analysis.
- `work/lapq/phase3`: four-arm ablations, correctness/trace gates, and raw results.
- `work/lapq/phase4`: current two-host analysis, returned native/retention records,
  hardware observations, and comparisons. Imported scripts are preserved source
  evidence; the quickstart does not execute them.
- `work/lapq/phase5`: standalone issue reproducer and retention figure styling.
- `work/lapq/phase6`: the layered-archive verifier and release audit utilities.
- `provenance`: unchanged historical checksum lists and this snapshot's source map.

This is a curated working snapshot, not a byte-for-byte replacement of the full
historical artifact. Older PDFs, nested archives, foreign executables, caches,
installed dependencies, external source/data, and internal editorial notes are
excluded. The full historical two-host artifact can be supplied separately as a
release asset; it is not embedded here. `verify_current_study.py` applies to a
full restoration with that separate artifact; `verify_snapshot.py` verifies this
curated repository. Historical checksum lists are not rewritten and should not
be used as the snapshot's file list.

Raw observations preserve their exact historical bytes, including local home
paths, machine-name fields, and runtime metadata. This is a **named public-review
snapshot, not an anonymized artifact**. Absolute historical paths are provenance;
the supported analyses resolve current files relative to this tree. No credentials
were found by the bounded audit; this is not a comprehensive secrecy guarantee.

## Licensing and citation

`LICENSE` applies only to new author-owned software/documentation in this
repository. It does not grant rights to external upstream source, datasets,
third-party software, historical raw records, or future publisher templates.
See `THIRD_PARTY_NOTICES.md` and `DATA_SOURCES.md` for exclusions and attribution.
No blanket MIT grant is asserted for material the author does not own.
Use `CITATION.cff` to cite this software snapshot. No DOI, repository URL,
affiliation, email, or ORCID is invented. A journal manuscript is not included
until the author supplies the finalized source. No repository has been published
by building this folder.
'''

NOTICES = '''# Licensing scope and third-party notices

The MIT license covers new software/documentation authored for this audit by
Somama Siddiqui using Codex. It is not a license for the original Benomar–Coester
implementation, separately obtained data, dependencies, or third-party content.
Raw factual observations are preserved as evidence; no blanket ownership/license
claim is made over third-party-derived data or quoted metadata. Copyrightable
new analysis software is separable from these records.

## Original priority-queue implementation

Ziyad Benomar and Christian Coester, *Learning-Augmented Priority Queues* (2024).
Repository: https://github.com/Ziyad-Benomar/Learning-augmented-priority-queues
Pinned commit: `3915cc261e87949b2b0aa724673c2227492d1b08`.
The complete pinned tree inspected on 6 November 2026 contains no license file;
no explicit source license grant was established. The original source is **not
redistributed** here: `fetch_inputs.py` obtains the pinned public files separately.
Public access is not a blanket permission to redistribute or relicense them.
Adapters load that external implementation; the MIT license covers our new
adapter code only, not the loaded classes. Resolve any additional permissions
needed for your intended redistribution, particularly modified upstream source.

## OpenStreetMap / Brussels

Brussels graph data is derived from OpenStreetMap. © OpenStreetMap contributors.
OpenStreetMap data is available under the Open Database License (ODbL):
https://www.openstreetmap.org/copyright
https://opendatacommons.org/licenses/odbl/1-0/
The original GraphML is separately fetched from the pinned research repository.
Record provenance and attribution when using it or associated derived outputs.
Binary shortest-path arrays in this repository are computed validation outputs;
they are not represented as MIT-licensed substitutes for the source database.
Applicable database/produced-work obligations should be assessed for any further
redistribution; this notice does not claim to settle every derivative-work case.

## DIMACS / New York

The 9th DIMACS Shortest Paths Challenge identifies its files as public domain
unless otherwise stated. Its NY core graph has 264,346 vertices and 733,846 arcs:
https://www.diag.uniroma1.it/~challenge9/download.shtml
The indexed official page was available during the audit; its direct legacy
endpoint returned 404. The graph header identifies TIGER/Line and DIMACS. The
identical-byte download mirror is Training By Packt's repository; its root MIT
notice is © 2019 Training By Packt. Original dataset provenance is retained and
is not replaced by that repository-level software notice.
https://github.com/TrainingByPackt/CPP-Data-Structures-and-Algorithm-Design-Principles/blob/master/LICENSE

## Dependencies and historical records

Dependencies are installed separately and retain their own license terms; their
source/wheels are not vendored here. Their versions are pinned in requirements.
The returned Windows/WSL capture/wrapper code and the independent native code
were written for this study; historical platform/compiler/version strings and
logs remain factual provenance. A separate full historical artifact retains
its own unmodified contents and is not relicensed wholesale by this repository.
'''


def write_manifest(destination, provenance):
    files = {p.relative_to(destination).as_posix(): {'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())}
             for p in sorted(destination.rglob('*')) if p.is_file() and p.name!='snapshot-manifest.json'
             and '__pycache__' not in p.parts}
    manifest = {'format':'learned-pq-curated-repository-v1','files':files,
                'source_artifact_sha256':sha(ARCHIVE.read_bytes()), 'source_map':provenance,
                'scope':'New named source/results snapshot; not a replacement for unchanged historical archives.'}
    (destination/'snapshot-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')


def build():
    if DEST.exists():
        raise RuntimeError('Destination exists; preserve the reviewed repository instead of overwriting it')
    outer, manifest, returned, original, base, overlay = restorer().load_bundle(archive_path=ARCHIVE)
    combined = dict(base)
    combined.update(original)
    combined.update(overlay)
    selected = {n:d for n,d in combined.items() if retain(n)}
    for name, data in returned.items():
        allowed = name.startswith(('second_host_native/','second_host_retention/','hardware_and_logs/','reproduction/','comparison/'))
        p = Path(name)
        if allowed and p.suffix in {'.py','.cpp','.json','.csv','.txt','.log','.sha256','.f64le','.sh','.ps1'}:
            selected['work/lapq/phase4/imported/'+name] = data
    for n in ['fetch_inputs.py','input_manifest.json','requirements.txt']:
        selected[n] = base[n]
    for n in ['verify_current_study.py','artifact_release_audit.py','repository_build.py']:
        selected['work/lapq/phase6/'+n]=(HERE/n).read_bytes()
    selected['provenance/historical-checksums/base-artifact-checksums.json']=base['artifact_checksums.json']
    selected['provenance/historical-checksums/first-host-completed-study-manifest.json']=original['completed-study-manifest.json']
    selected['provenance/historical-checksums/two-host-manifest.json']=outer['two-host-manifest.json']
    selected['provenance/historical-checksums/returned-archive-manifest.json']=returned['archive-manifest.json']
    for name in ['learned-pq-bounded-live-retention.png','learned-pq-ablation-memory.png','learned-pq-two-host-native.png']:
        selected['figures/'+name]=(ROOT/'outputs'/name).read_bytes()
    DEST.mkdir()
    provenance={}
    for name,data in selected.items():
        path=DEST/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        provenance[name]={'sha256':sha(data),'bytes':len(data),'origin':'verified historical layer or current coordinator utility; experimental bytes unchanged'}
    (DEST/'README.md').write_text(README)
    (DEST/'LICENSE').write_text(LICENSE)
    (DEST/'THIRD_PARTY_NOTICES.md').write_text(NOTICES)
    (DEST/'DATA_SOURCES.md').write_text('# Input provenance\n\nThe root `input_manifest.json` is the unchanged download/local SHA256 manifest.\nThree source files reproduce an appended terminal newline; graph inputs are not normalized.\nBrussels preserves released key-0 parallel-edge semantics; New York preserves every\nDIMACS arc. Synthetic workloads, seeds, ordered adjacency hashes, and source splits\nare frozen in `work/lapq/phase2/workload_manifest.json` and its protocol.\n\nRead `THIRD_PARTY_NOTICES.md` for source/data attribution and license boundaries.\nNY uses a moving mirror branch but accepts only the pinned bytes; a changed or\nunavailable endpoint is a failed input retrieval, not permission to substitute data.\n')
    (DEST/'CITATION.cff').write_text('cff-version: 1.2.0\nmessage: "Please cite this software snapshot when using its code or results."\ntype: software\ntitle: "Learning-Augmented Priority Queue Audit"\nversion: "0.1.0"\nauthors:\n  - family-names: "Siddiqui"\n    given-names: "Somama"\nabstract: "Source, frozen plans, raw observations, and analysis for an empirical audit of learning-augmented priority queues. Licensing scope is documented in THIRD_PARTY_NOTICES.md."\n')
    (DEST/'requirements-figures.txt').write_text('matplotlib==3.10.1\nnumpy==2.2.4\n')
    (DEST/'requirements-report.txt').write_text('matplotlib==3.10.1\nnumpy==2.2.4\nreportlab==4.4.9\n')
    (DEST/'verify_snapshot.py').write_text(VERIFY)
    (DEST/'.gitignore').write_text('.venv/\n__pycache__/\n*.pyc\nreanalysis/\nwork/lapq/upstream/\nwork/lapq/data/\nwork/data/\n**/mpl-cache/\n**/mplconfig/\n**/rerun*/\n*.zip\n')
    write_manifest(DEST,provenance)
    return provenance


def validate(provenance):
    calls=[]
    with tempfile.TemporaryDirectory(prefix='repository-validation-',dir=HERE) as temp:
        copy=Path(temp)/'repository';shutil.copytree(DEST,copy)
        def run(arguments):
            result=subprocess.run([sys.executable,*arguments],cwd=copy,text=True,capture_output=True,timeout=240)
            calls.append({'argv':arguments,'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
            if result.returncode:raise RuntimeError(json.dumps(calls[-1]))
        run(['verify_snapshot.py'])
        run(['work/lapq/phase4/native_cross_host_analysis.py','--output','reanalysis/native.json','--tables','reanalysis/tables'])
        # External inputs are copied only into this disposable copy, never published.
        for item in json.loads((copy/'input_manifest.json').read_text()):
            data=(ROOT/item['target']).read_bytes()
            if sha(data)!=item['local_sha256']:raise ValueError('Existing pinned input mismatch')
            path=copy/item['target'];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        run(['fetch_inputs.py','--check-only'])
        before=json.loads((copy/'work/lapq/phase4/retention_cross_host_summary.json').read_text())
        run(['work/lapq/phase4/retention_cross_host_analysis.py'])
        after=json.loads((copy/'work/lapq/phase4/retention_cross_host_summary.json').read_text())
        for key in ('coverage','rows','within_host_cleanup_comparisons','memory_diagnostics'):
            if before[key]!=after[key]:raise ValueError('Retention scientific output changed: '+key)
        old_native=json.loads((copy/'work/lapq/phase4/native_cross_host_summary.json').read_text())
        new_native=json.loads((copy/'reanalysis/native.json').read_text())
        for key in ('hosts','side_by_side_contrasts','side_by_side_rankings','direction_changes','headline'):
            if old_native[key]!=new_native[key]:raise ValueError('Native scientific output changed: '+key)
    result={'status':'passed','source_artifact_sha256':sha(ARCHIVE.read_bytes()),'calls':calls,
            'native_both_host_timings':6888,'retention_matched_cells':30,'scientific_outputs_exactly_reproduced':True,
            'benchmarks_executed':False,'imported_executables_run':False,'external_inputs_only_in_disposable_copy':True,
            'snapshot':str(DEST.relative_to(ROOT)),'builder_sha256':sha(Path(__file__).read_bytes())}
    (HERE/'repository_validation.json').write_text(json.dumps(result,indent=2)+'\n')
    public={k:v for k,v in result.items() if k!='calls'}
    public['checks']=[{'command':c['argv'],'returncode':c['returncode']}for c in calls]
    (DEST/'provenance/repository-validation.json').write_text(json.dumps(public,indent=2)+'\n')
    write_manifest(DEST,provenance)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--validate',action='store_true')
    args=parser.parse_args();provenance=build()
    result=validate(provenance) if args.validate else {'status':'built_not_validated'}
    print(json.dumps({'status':result['status'],'destination':str(DEST),'files':len(json.loads((DEST/'snapshot-manifest.json').read_text())['files'])}))
