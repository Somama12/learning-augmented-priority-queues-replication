#!/usr/bin/env python3
"""Integrate completed ReScience work into the existing curated repository.

This is a post-run packaging operation, never an experiment or manuscript build.
It refuses pending/failed results, a placeholder paper, or a missing final PDF.
The existing phase2–5 scientific files are verified and preserved byte-for-byte.
No original paper PDF, upstream Python source, foreign executable, or ZIP is copied.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
REPOSITORY = ROOT/'outputs/learned-priority-queue-audit-repository'
MAIN_PDF = ROOT/'outputs/learned-priority-queues-rescience-c.pdf'
EXTENSION_PDF = ROOT/'outputs/learned-priority-queues-runtime-memory-supplement.pdf'
LEGACY_EXTENSION_PDF = ROOT/'outputs/learned-priority-queues-manuscript-draft.pdf'
SOURCE_NAMES = (
    'replication.cpp', 'input_generation.py', 'experiment_plan.py', 'experiment_plan.json',
    'run_replication.py', 'frozen_protocol.json', 'engine_preflight.py', 'engine_preflight.json',
    'input_generation_validation.json', 'extract_reference_curves.py',
    'published_reference_curves.json', 'reference_provenance.json', 'analyze_replication.py',
    'reproduce_replication.py', 'replication_summary.json', 'update_repository_for_rescience.py',
    'plot_replication.py', 'independent_count_audit.py', 'independent_count_audit.json',
    'independent_count_audit.txt',
)
OPTIONAL_NAMES = ('portable_validation.json', 'portable_runner_validation.json', 'replication_analysis_validation.json', 'document_validation.json')
PAPER_NAMES = {'article.tex','article.py','content.tex','header.tex','metadata.tex','metadata.yaml',
               'bibliography.bib','rescience.cls','COPYING','README.md','BUILD_NOTES.txt',
               'yaml-to-latex.py','build_manifest.json'}
FONT_FOLDERS = {'roboto','source-code-pro','source-sans-pro','source-serif-pro'}
PROTECTED = tuple(f'work/lapq/phase{i}/' for i in (2,3,4,5))
PUBLIC_REPOSITORY_URL = 'https://github.com/Somama12/learning-augmented-priority-queues-replication'
PUBLISHED_SOURCE_COMMIT = 'd71f1494c0f02f70b40216f69af1158a12db3d70'
PUBLISHED_SOURCE_TREE = 'a264adaeb761a50026a89e55d2c86aae3bb1dc3c'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(path.read_text())


def require(value, message):
    if not value:
        raise RuntimeError(message)


def public_repository():
    metadata = read(HERE/'public_repository.json')
    require(metadata.get('repository_url') == PUBLIC_REPOSITORY_URL,
            'Public repository URL differs from the confirmed repository')
    require(metadata.get('visibility') == 'public' and metadata.get('source_snapshot_published') is True,
            'Public scientific source publication has not been verified')
    require(metadata.get('source_commit') == PUBLISHED_SOURCE_COMMIT
            and metadata.get('source_tree') == PUBLISHED_SOURCE_TREE,
            'Public source snapshot differs from the independently verified commit/tree')
    require(metadata.get('journal_submitted') is False,
            'Journal status changed; review release wording before integration')
    return metadata


FETCH_REFERENCE = '#!/usr/bin/env python3\n"""Fetch separately distributed references; never execute downloaded source."""\nimport argparse,hashlib,json\nfrom pathlib import Path\nimport urllib.request\nhere=Path(__file__).resolve().parent\nparser=argparse.ArgumentParser(description=__doc__)\nparser.add_argument(\'--check-only\',action=\'store_true\')\nparser.add_argument(\'--include-oracle\',action=\'store_true\',help=\'Also fetch four original Python oracle files for optional differential checks\')\nargs=parser.parse_args()\nmanifest=json.loads((here/\'reference_fetch_manifest.json\').read_text())\nfor item in manifest:\n if item[\'kind\']==\'oracle\' and not args.include_oracle:continue\n target=(here/item[\'target\']).resolve()\n if here.parent not in target.parents:raise SystemExit(\'Unsafe reference target\')\n if target.exists():\n  if hashlib.sha256(target.read_bytes()).hexdigest()!=item[\'local_sha256\']:\n   raise SystemExit(\'Existing reference differs; preserve it and resolve discrepancy: \'+str(target))\n  print(\'Verified:\',item[\'target\']);continue\n if args.check_only:raise SystemExit(\'Missing reference: \'+item[\'target\'])\n request=urllib.request.Request(item[\'url\'],headers={\'User-Agent\':\'LAPQ-partial-replication-reference/1.0\'})\n with urllib.request.urlopen(request,timeout=120) as response:data=response.read()\n if hashlib.sha256(data).hexdigest()!=item[\'download_sha256\']:raise SystemExit(\'Download hash differs: \'+item[\'target\'])\n if item.get(\'git_blob_sha1\'):\n  blob=hashlib.sha1(b\'blob \'+str(len(data)).encode()+b\'\\0\'+data).hexdigest()\n  if blob!=item[\'git_blob_sha1\']:raise SystemExit(\'Git blob hash differs: \'+item[\'target\'])\n if item[\'normalization\']==\'append_one_newline\':data+=b\'\\n\'\n elif item[\'normalization\']!=\'none\':raise SystemExit(\'Unknown normalization\')\n if hashlib.sha256(data).hexdigest()!=item[\'local_sha256\']:raise SystemExit(\'Normalized hash differs: \'+item[\'target\'])\n target.parent.mkdir(parents=True,exist_ok=True)\n temporary=target.with_suffix(target.suffix+\'.download.tmp\');temporary.write_bytes(data);temporary.replace(target)\n print(\'Fetched and verified:\',item[\'target\'])\n'

README = '''# Learning-augmented priority queues: partial replication and artifact audit

Author: **Sahil Siddiqui**. The author is responsible for all claims. AI coding
assistants were used as tools for code generation, debugging, and editing of prose.

Public repository: [learning-augmented-priority-queues-replication](__PUBLIC_ROOT__).
The independently verified [scientific source snapshot](__PUBLIC_ROOT__/tree/__PUBLIC_SOURCE__)
is commit `__PUBLIC_SOURCE__`. This stable source commit precedes the current
paper/documentation update; it is not presented as the commit of this README.
Source publication does not imply journal submission or acceptance.

## Main paper and scope

`paper/learned-priority-queues-rescience-c.pdf` is the ReScience C preparation.
Editable source, figures, template class and licensed fonts are in
`paper/rescience/`. This is a **partial replication** of clean-comparison sorting
curves from Benomar and Coester (2024): offline rank predictions, online rank
predictions, and dirty/clean comparisons; class and decay settings; n = 1,000,
10,000 and 100,000; 21 parameter levels and 30 repetitions. There are 3,780
shared conditions and 11,340 model runs.

The new C++ implementation was checked against the pinned released Python on
small traces. It is independently authored but not a clean-room implementation.
This does not replicate every original algorithm, the graph experiments, or
the theoretical proofs. The metric is clean comparisons, not elapsed speed.
Common seeded inputs/heights and multinomial sampling of final decay ranks are
documented departures from the original unseeded random-stream arrangement.
The original mathematical decay distribution is preserved; finite PRNG outputs
are not claimed identical. All pointwise discrepancies and limitations remain
in the analysis rather than being hidden behind a single success label.

The main paper proceeds from the comparison-count replication to the historical
node-map retention defect, its narrow cleanup, and a short runtime comparison.
The full supporting runtime, memory, ablation and two-host analyses are in
`supplement/learned-priority-queues-runtime-memory-supplement.pdf`; their scientific
records remain unchanged under `work/lapq/phase2` through `phase5`. Those timings
are additional implementation evidence and do not substitute for replication of
the original comparison-count results.

## Verify and reanalyze existing evidence

From the repository root, with Python 3.12 or 3.13:

```sh
python3 verify_snapshot.py
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python work/lapq/phase7/reproduce_replication.py --verify-only
.venv/bin/python work/lapq/phase7/fetch_replication_references.py
.venv/bin/python work/lapq/phase7/analyze_replication.py --results work/lapq/phase7/results --output reanalysis/replication_summary.json
```

The original paper PDF is fetched separately from its public official URL and
accepted only at its frozen hash. It is intentionally not redistributed in this
repository. The supplied n = 1,000 author aggregates and extracted plotted means
retain their provenance; aggregate standard deviations are not reinterpreted as
standard errors or raw trials. Analysis writes tables beside the selected output.
These commands do not run the sorting experiments or execute imported binaries.

## Portable experimental reproduction

A GCC/Clang-compatible C++17 compiler is required. Use a quiet machine, fresh
destinations, and sequential execution. The portable runner checks every frozen
source/input/evidence file, compiles the source locally, and records the new
compiler and executable hash. The original platform-specific executable hash is
historical provenance, not a requirement that another compiler reproduce it.

Predetermined quick check: six conditions (maximum parameter, repetition zero,
one per size/setting), 18 model runs. **This is not full paper evidence.**

```sh
.venv/bin/python work/lapq/phase7/reproduce_replication.py --mode quick --destination reruns/sorting-quick
```

Complete frozen plan, followed by analysis of that separate run:

```sh
.venv/bin/python work/lapq/phase7/reproduce_replication.py --mode full --destination reruns/sorting-full
.venv/bin/python work/lapq/phase7/analyze_replication.py --results reruns/sorting-full --output reanalysis/full-rerun/replication_summary.json
```

The full-grid analysis deliberately rejects quick/incomplete runs. No destination
overwrite or resume is supported by the portable runner; preserve failed outputs
and choose a new directory. Raw responses, input seeds/hashes and failures are
retained. Elapsed progress messages are not performance results. Original frozen
sources and records are never rewritten to accommodate a new machine.

Original Python source is not bundled. For optional differential validation,
work in a **disposable repository copy**: the preflight writes its compiled builds
and the extraction/plot/audit scripts write their standard output paths.

```sh
.venv/bin/python -m pip install -r requirements-figures.txt -r requirements-reference.txt
.venv/bin/python work/lapq/phase7/fetch_replication_references.py --include-oracle
.venv/bin/python work/lapq/phase7/engine_preflight.py --compiler c++ --output preflight-rerun.json
.venv/bin/python work/lapq/phase7/extract_reference_curves.py
.venv/bin/python work/lapq/phase7/plot_replication.py
.venv/bin/python work/lapq/phase7/independent_count_audit.py
```

The fetch manifest verifies original download SHA256/Git blob hashes, then the
same explicit one-newline normalization used by the saved differential preflight.
The added newline does not change Python semantics. Downloads are never executed
by the fetch helper; the optional preflight explicitly imports the pinned oracle.
It runs 666 small differential cases with optimized and sanitizer builds, not the
primary grid. Use `--skip-sanitizer` only when your compiler lacks those sanitizers
and report that reduced validation. Saved checks are historical evidence, not a
claim that a reviewer has rerun them. Matplotlib is required for plots and by the
original skip-list oracle imports; pypdf is needed only for optional PDF extraction.
The existing `fetch_inputs.py` separately retrieves the earlier study's inputs.

## Supporting retention and runtime analyses

```sh
.venv/bin/python work/lapq/phase4/native_cross_host_analysis.py --output reanalysis/native.json --tables reanalysis/native-tables
.venv/bin/python fetch_inputs.py
.venv/bin/python work/lapq/phase4/retention_cross_host_analysis.py
```

Run the last command in a disposable copy because it writes the shipped summary
path. These analyses preserve host separation and the allocation/timing scope.
The full historical artifact may be offered as a separate release
asset. Nested historical ZIPs, old executable builds and the original paper PDF
are not embedded in this repository. Historical checksum lists are preserved
unchanged under `provenance/historical-checksums/`; use `verify_snapshot.py` for
this curated tree rather than applying an old layer's file list to new overlays.

## Paper source and licenses

The paper source requires XeLaTeX and BibTeX; see `paper/rescience/BUILD_NOTES.txt`
for template adaptations. From `paper/rescience/`, use:

```sh
xelatex article.tex
bibtex article
xelatex article.tex
xelatex article.tex
```

Tectonic is an alternative if installed. The supplied TeX is self-contained;
the historical manuscript-staging builder and its private workspace dependencies
are not a supported repository command. Compile only after experiments finish.
ReScience template GPL and font Apache/SIL notices are preserved alongside the
files. The root MIT license applies to new author-owned audit/replication software
and documentation; it does not relicense the template, fonts, original Python,
source datasets, author aggregates, or publication text. Read
`THIRD_PARTY_NOTICES.md` and `DATA_SOURCES.md` for attribution and scope.

This is a named repository. Exact raw records retain historical local paths and
machine metadata. It is not an anonymized submission artifact. The public
repository and verified source commit are recorded in `provenance/public-repository.json`.
No DOI, affiliation, ORCID, journal acceptance or journal publication date is invented.

## Public release checklist

- [x] Full planned records and analysis are complete and pass the declared audit.
- [x] Source, plan, input-hash and result provenance accompany the observations.
- [x] Earlier phase2–5 scientific records are preserved byte-for-byte.
- [x] Original paper/Python and foreign executable builds are excluded.
- [x] Template/font notices are retained and distinguished from the MIT scope.
- [x] Portable quick-run validation matches all six primary input hashes and all 18 outputs.
- [ ] Check the documented machine/compiler prerequisites on the reviewer host.
- Optional: add affiliation or ORCID only if supplied; absent fields remain blank.
- [x] The scientific source snapshot is public and its complete Git tree was independently verified.
- [x] Repository and immutable source-snapshot links are supplied in this README and citation metadata.
- [ ] Review named sharing of raw local paths and third-party licensing boundaries.
- [ ] Add the unchanged full historical artifact as a separate release asset if desired.
- [ ] Complete the current ReScience C submission form using the actual release URLs.

The scientific source is public at the links above. The paper is prepared for
ReScience C; no journal submission or acceptance is claimed. A future document
update must not describe its own not-yet-created commit as already published.
'''

NOTICE = '''

## ReScience partial replication, template and reference material

New phase7 software is included within the author-owned MIT scope. The original
Benomar–Coester Python source and paper PDF are not bundled and are not licensed
by this repository. The released n=1,000 aggregate values and PDF-derived plotted
means are attributed to the original authors, with hashes and provenance in
phase7; no blanket MIT claim is made for those external reference materials.

`paper/rescience/` contains adapted ReScience template sources. Its original
`COPYING` (GNU GPL version3) is retained; modifications are identified in
`BUILD_NOTES.txt`. Roboto's Apache notice and the Source font families' SIL Open
Font License notices are preserved in their respective subdirectories. These
files retain their own terms and are explicitly outside the root MIT grant.
The author's manuscript is not automatically relicensed by the software license;
its publication/license declarations follow the author-approved journal metadata.
'''


def load_builder():
    path = HERE.parent/'phase6/repository_build.py'
    spec = importlib.util.spec_from_file_location('curated_repository_builder', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collect_files(repository, paper_source):
    publication = public_repository()
    execution = read(HERE/'results/execution.json')
    summary = read(HERE/'replication_summary.json')
    frozen = read(HERE/'frozen_protocol.json')
    built = read(paper_source/'build_manifest.json')
    require(execution.get('status') == 'passed' and not execution.get('failures'), 'Primary run is not successful')
    require(execution['completed_conditions'] == execution['planned_conditions'] == 3780
            and execution['planned_method_runs'] == 11340, 'Primary run incomplete')
    require(summary.get('complete') is True and summary.get('audit', {}).get('passed') is True,
            'Primary analysis incomplete or failed')
    require(built.get('results_complete_and_audited') is True and built.get('placeholder_draft') is False,
            'Paper source still has placeholders or unvalidated results')
    require(built.get('summary_sha256') == sha((HERE/'replication_summary.json').read_bytes()),
            'Paper source uses a different summary')
    require(MAIN_PDF.is_file() and MAIN_PDF.read_bytes().startswith(b'%PDF-'), 'Final main PDF is unavailable')
    require(EXTENSION_PDF.is_file() and EXTENSION_PDF.read_bytes().startswith(b'%PDF-'), 'Extension PDF is unavailable')
    byline = read(HERE/'preferred_name_revision.json')
    require(byline['status'] == 'passed' and byline['supplement_text_only_byline_changed'] is True
            and byline['original_pdf_sha256'] == sha(LEGACY_EXTENSION_PDF.read_bytes())
            and byline['updated_pdf_sha256'] == sha(EXTENSION_PDF.read_bytes())
            and byline['old_display_name'] == 'Somama Siddiqui'
            and byline['new_display_name'] == 'Sahil Siddiqui',
            'Supplement byline-only derivative failed its provenance check')
    for name, digest in frozen['source_sha256'].items():
        require(sha((HERE/name).read_bytes()) == digest, 'Frozen source/evidence changed: '+name)
    require(execution['condition_file_sha256'] == sha((HERE/'results/conditions.jsonl').read_bytes()),
            'Primary conditions changed')
    require(execution['raw_engine_file_sha256'] == sha((HERE/'results/raw-engine-output.jsonl').read_bytes()),
            'Raw engine output changed')
    files = {f'work/lapq/phase7/{name}':(HERE/name).read_bytes() for name in SOURCE_NAMES}
    for name in OPTIONAL_NAMES:
        if (HERE/name).is_file(): files['work/lapq/phase7/'+name]=(HERE/name).read_bytes()
    for folder in ('results','tables','portable_validation','figures'):
        require((HERE/folder).is_dir(), 'Missing completed evidence folder: '+folder)
        for path in (HERE/folder).rglob('*'):
            if path.is_file() and path.suffix in {'.json','.jsonl','.csv','.txt','.png','.pdf'}:
                files['work/lapq/phase7/'+path.relative_to(HERE).as_posix()]=path.read_bytes()
    for name in ('data_class_1000.json','data_decay_1000.json'):
        files['work/lapq/phase7/sources/'+name]=(HERE/'sources'/name).read_bytes()
    files['work/lapq/phase7/template/fetch_manifest.json']=(HERE/'template/fetch_manifest.json').read_bytes()
    for path in paper_source.rglob('*'):
        if not path.is_file() or path.is_symlink(): continue
        relative=path.relative_to(paper_source)
        allowed=((relative.parts[0] in FONT_FOLDERS and path.suffix.lower() in {'.ttf','.otf','.txt'})
                 or relative.as_posix() in PAPER_NAMES
                 or (path.name.startswith('figure-') and path.suffix in {'.png','.pdf','.svg'}))
        if allowed:
            data=path.read_bytes()
            if relative.as_posix()=='BUILD_NOTES.txt':
                text=data.decode()
                text='\n'.join(line for line in text.splitlines() if not line.startswith('Final staging:'))
                text+='\nRepository build: from paper/rescience, run xelatex article.tex; bibtex article; xelatex article.tex; xelatex article.tex. Tectonic is an alternative.\nThe historical staging command depends on authoring files outside this repository and is intentionally not advertised here.\n'
                data=text.encode()
            files['paper/rescience/'+relative.as_posix()]=data
    require('paper/rescience/COPYING' in files, 'Template license missing')
    for folder in FONT_FOLDERS:
        require(any(n.startswith('paper/rescience/'+folder+'/') and 'License' in n for n in files),
                'Font license missing: '+folder)
    files['paper/learned-priority-queues-rescience-c.pdf']=MAIN_PDF.read_bytes()
    files['supplement/learned-priority-queues-runtime-memory-supplement.pdf']=EXTENSION_PDF.read_bytes()
    files['provenance/preferred-name-revision.json']=(HERE/'preferred_name_revision.json').read_bytes()
    files['provenance/public-repository.json']=(HERE/'public_repository.json').read_bytes()
    files['work/lapq/phase7/fetch_replication_references.py']=FETCH_REFERENCE.encode()
    pdf_digest=read(HERE/'published_reference_curves.json')['source_pdf_sha256']
    provenance=read(HERE/'reference_provenance.json')
    reference_manifest=[{'kind':'paper','target':'sources/original-paper.pdf',
                        'url':provenance['pdf_source_url'],'download_sha256':pdf_digest,
                        'local_sha256':pdf_digest,'normalization':'none'}]
    preflight=read(HERE/'engine_preflight.json')
    git_blobs={'skiplist.py':'168d39c1107892044884559bc19fc9a7faf81821',
               'sorting.py':'211374e66719d815f2d859dc9ad001d9a8c8a086',
               'predictions.py':'68f60b4d96fbfe1d5d942d2706adfdfd2bd624ed',
               'heaps.py':'eadb3369dec983ac7d70fc97bb804b6e61aa6251'}
    for name,local_digest in preflight['oracle_source_sha256'].items():
        local=(HERE.parent/'upstream'/name).read_bytes()
        require(sha(local)==local_digest and local.endswith(b'\n'),'Oracle hash mismatch: '+name)
        raw=local[:-1]
        blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        require(blob==git_blobs[name],'Oracle original Git blob mismatch: '+name)
        reference_manifest.append({'kind':'oracle','target':'../upstream/'+name,
            'url':'https://raw.githubusercontent.com/Ziyad-Benomar/Learning-augmented-priority-queues/'+preflight['upstream_commit']+'/'+name,
            'download_sha256':sha(raw),'local_sha256':local_digest,
            'git_blob_sha1':blob,'normalization':'append_one_newline'})
    files['work/lapq/phase7/reference_fetch_manifest.json']=(json.dumps(reference_manifest,indent=2)+'\n').encode()
    files['requirements-reference.txt']=b'pypdf==6.10.0\n'
    files['requirements-figures.txt']=b'matplotlib==3.10.1\nnumpy==2.2.4\n'
    citation=(repository/'CITATION.cff').read_text().replace('given-names: "Somama"', 'given-names: "Sahil"')
    citation='\n'.join(('title: "Learning-Augmented Priority Queues: Partial Replication and Artifact Audit"' if line.startswith('title:') else 'version: "0.2.0-preparation"' if line.startswith('version:') else 'abstract: "Source, frozen plans, comparison-count observations, and analysis for a partial replication of learning-augmented priority queues, with a retention-defect cleanup and supporting two-host runtime and memory analyses. Licensing scope is documented in THIRD_PARTY_NOTICES.md."' if line.startswith('abstract:') else line) for line in citation.splitlines())+'\n'
    citation='\n'.join(line for line in citation.splitlines()
                       if not line.startswith(('repository-code:', 'url:')))+'\n'
    citation+='repository-code: '+json.dumps(publication['repository_url'])+'\n'
    citation+='url: '+json.dumps(publication['repository_url']+'/tree/'+publication['source_commit'])+'\n'
    files['CITATION.cff']=citation.encode()
    files['README.md']=README.replace('__PUBLIC_ROOT__',publication['repository_url']).replace('__PUBLIC_SOURCE__',publication['source_commit']).encode()
    notice=(repository/'THIRD_PARTY_NOTICES.md').read_text().replace('Somama Siddiqui', 'Sahil Siddiqui')
    files['LICENSE']=(repository/'LICENSE').read_text().replace('Somama Siddiqui', 'Sahil Siddiqui').encode()
    if '## ReScience partial replication, template and reference material' not in notice:
        notice+=NOTICE
    files['THIRD_PARTY_NOTICES.md']=notice.encode()
    ignore=(repository/'.gitignore').read_text()
    for line in ('work/lapq/phase7/sources/original-paper.pdf','work/lapq/upstream/','reruns/'):
        if line not in ignore.splitlines(): ignore+='\n'+line+'\n'
    files['.gitignore']=ignore.encode()
    return files, execution, summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository',type=Path,default=REPOSITORY)
    parser.add_argument('--paper-source',type=Path,default=HERE/'rescience_draft')
    parser.add_argument('--check-only',action='store_true')
    args=parser.parse_args()
    repository=args.repository.resolve()
    require(repository.is_dir(), 'Existing curated repository is required; no historical rebuild is performed')
    manifest=read(repository/'snapshot-manifest.json')
    for name, record in manifest['files'].items():
        if '.git' in Path(name).parts:
            continue
        require((repository/name).is_file() and sha((repository/name).read_bytes())==record['sha256'],
                'Existing snapshot changed; review and refresh its manifest first: '+name)
    protected={name:record['sha256'] for name,record in manifest['files'].items() if name.startswith(PROTECTED)}
    files,execution,summary=collect_files(repository,args.paper_source)
    require(not any(name.startswith(PROTECTED) for name in files), 'Integration would touch older scientific records')
    if args.check_only:
        print(json.dumps({'status':'ready_to_integrate','new_or_updated_files':len(files),
                          'preserved_phase2_to_phase5_files':len(protected),'repository':str(repository)},indent=2))
        return
    for name,data in files.items():
        path=repository/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    for name,digest in protected.items():
        require(sha((repository/name).read_bytes())==digest,'Older scientific record changed: '+name)
    publication=public_repository()
    receipt={'status':'integrated','created_utc':datetime.now(timezone.utc).isoformat(),
             'phase7_freeze_sha256':sha((HERE/'frozen_protocol.json').read_bytes()),
             'phase7_summary_sha256':sha((HERE/'replication_summary.json').read_bytes()),
             'primary_conditions':3780,'primary_method_runs':11340,
             'main_pdf_sha256':sha(MAIN_PDF.read_bytes()),'extension_pdf_sha256':sha(EXTENSION_PDF.read_bytes()),
             'preserved_phase2_to_phase5_files':len(protected),
             'added_or_updated_files':{name:sha(data) for name,data in files.items()},
             'excluded':['original paper PDF','original Python source','compiled engines','nested archives'],
             'published':publication['source_snapshot_published'],
             'publication_scope':'Independently verified scientific source snapshot; publication of this new integration is not asserted',
             'public_repository_url':publication['repository_url'],
             'published_source_commit':publication['source_commit'],
             'published_source_tree':publication['source_tree'],
             'public_repository_evidence_sha256':sha((HERE/'public_repository.json').read_bytes()),
             'submitted':publication['journal_submitted'],'helper_sha256':sha(Path(__file__).read_bytes())}
    (repository/'provenance/rescience-integration.json').write_text(json.dumps(receipt,indent=2)+'\n')
    source_map=manifest.get('source_map',{})
    for name,data in files.items():
        source_map[name]={'sha256':sha(data),'bytes':len(data),'origin':'Completed phase7 replication or licensed ReScience paper source; previous scientific records unchanged'}
    final=dict(manifest)
    final['files']={p.relative_to(repository).as_posix():{'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())}
                   for p in sorted(repository.rglob('*')) if p.is_file()
                   and p.name!='snapshot-manifest.json'
                   and not {'.git','__pycache__'}.intersection(p.relative_to(repository).parts)}
    final['source_map']={name:record for name,record in source_map.items() if '.git' not in Path(name).parts}
    final['phase7_replication']={k:receipt[k] for k in ('phase7_freeze_sha256','phase7_summary_sha256','primary_conditions','primary_method_runs','main_pdf_sha256','extension_pdf_sha256',
        'public_repository_url','published_source_commit','published_source_tree','public_repository_evidence_sha256')}
    (repository/'snapshot-manifest.json').write_text(json.dumps(final,indent=2)+'\n')
    (HERE/'repository_integration.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({'status':'integrated','files':len(final['files']),
                      'preserved_phase2_to_phase5_files':len(protected)},indent=2))


if __name__=='__main__':main()
