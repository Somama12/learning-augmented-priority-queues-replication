# Learning-augmented priority queues: partial replication and artifact audit

**The frozen input generator requires exactly NumPy 2.2.4, including quick runs.
Create the pinned virtual environment below before running any verification,
analysis, preflight or reproduction command. Keep the version and hash checks
intact; a newer NumPy version is not a supported substitute.**

From the repository root, using Python 3.12 or 3.13:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c "import numpy; assert numpy.__version__ == '2.2.4', numpy.__version__; print('NumPy', numpy.__version__)"
```

Use `.venv/bin/python` for every Python command below. On Windows, use the
corresponding `.venv\Scripts\python.exe` executable. The requirements file
pins NumPy and the other required packages; the assertion confirms the active
interpreter is using the frozen generator version.

Author: **Sahil Siddiqui**. The author is responsible for all claims. AI coding
assistants were used as tools for code generation, debugging, and editing of prose.

Public repository: [learning-augmented-priority-queues-replication](https://github.com/Somama12/learning-augmented-priority-queues-replication).
Journal submission: [ReScience C #153](https://github.com/ReScience/submissions/issues/153) (submitted; not accepted).

Use the [v1.0.0 tagged source](https://github.com/Somama12/learning-augmented-priority-queues-replication/tree/v1.0.0) and
[v1.0.0 release page](https://github.com/Somama12/learning-augmented-priority-queues-replication/releases/tag/v1.0.0).
Artifact DOI: [10.5281/zenodo.24023742](https://doi.org/10.5281/zenodo.24023742).
The archive contains source, raw records, the paper, and the supplement.
Journal review status is separate from this software release.

## Main paper and scope

[Read the main paper](paper/learned-priority-queues-rescience-c.pdf),
[metadata](paper/rescience/metadata.yaml), and
[supplement](supplement/learned-priority-queues-runtime-memory-supplement.pdf).
The paper is prepared for ReScience C.
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

After completing the pinned environment setup above:

```sh
.venv/bin/python verify_snapshot.py
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

The quick and full modes both require NumPy 2.2.4 and enforce the frozen source
and input checks. The quick mode only reduces the number of conditions.

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
retained. Elapsed progress messages are not performance results. Frozen primary sources and records are preserved. The release source only splits
two ambiguous-looking C++ lines to remove compiler warnings; its exact formatting
revision is verified against the byte-for-byte original in
`work/lapq/phase7/frozen/replication.cpp`. `release-source-revision.json` records
both hashes, and the current runner builds the warning-free source.

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
The author uses ORCID 0009-0002-1033-2669 and the supplied affiliation Fisk
University. The artifact DOI was reserved for this release; no journal
acceptance or journal publication date is asserted.

## Public release checklist

- [x] Full planned records and analysis are complete and pass the declared audit.
- [x] Source, plan, input-hash and result provenance accompany the observations.
- [x] Earlier phase2–5 scientific records are preserved byte-for-byte.
- [x] Original paper/Python and foreign executable builds are excluded.
- [x] Template/font notices are retained and distinguished from the MIT scope.
- [x] Portable quick-run validation matches all six primary input hashes and all 18 outputs.
- [ ] Check the documented machine/compiler prerequisites on the reviewer host.
- [x] Author-supplied Fisk University affiliation and ORCID are recorded in paper and metadata.
- [x] The scientific source snapshot is public and its complete Git tree was independently verified.
- [x] Version v1.0.0 links and the artifact DOI are recorded in paper, README and citation metadata.
- [x] The active C++ formatting revision passes differential, quick-run and warning checks; the measured source is archived unchanged.
- [x] Historical local paths/name provenance remain disclosed and unchanged; component license notices are preserved.
- [x] Submitted to ReScience C as [issue #153](https://github.com/ReScience/submissions/issues/153) using the v1.0.0 release links.

The software release and journal review are separate. The artifact DOI identifies
the software/data archive; it is not a journal article DOI or an acceptance claim.
