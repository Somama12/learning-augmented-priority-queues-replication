# Learning-augmented priority queues: partial replication and artifact audit

Author: **Somama Siddiqui**. The author is responsible for all claims. AI coding
assistants were used as tools for code generation, debugging, and editing of prose.

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

The earlier two-host runtime and memory study is separate extension evidence:
`supplement/learned-priority-queues-runtime-memory-supplement.pdf`, with its unchanged records under
`work/lapq/phase2` through `phase5`. Those timings do not substitute for replication
of the original comparison-count results.

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

## Runtime/memory extension

```sh
.venv/bin/python work/lapq/phase4/native_cross_host_analysis.py --output reanalysis/native.json --tables reanalysis/native-tables
.venv/bin/python fetch_inputs.py
.venv/bin/python work/lapq/phase4/retention_cross_host_analysis.py
```

Run the last command in a disposable copy because it writes the shipped summary
path. These analyses preserve host separation and the allocation/timing scope.
The extension's full historical artifact may be offered as a separate release
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
machine metadata. It is not an anonymized submission artifact. No repository URL,
DOI, affiliation, ORCID, acceptance or publication date is invented.

## Public release checklist

- [x] Full planned records and analysis are complete and pass the declared audit.
- [x] Source, plan, input-hash and result provenance accompany the observations.
- [x] Earlier phase2–5 scientific records are preserved byte-for-byte.
- [x] Original paper/Python and foreign executable builds are excluded.
- [x] Template/font notices are retained and distinguished from the MIT scope.
- [x] Portable quick-run validation matches all six primary input hashes and all 18 outputs.
- [ ] Check the documented machine/compiler prerequisites on the reviewer host.
- Optional: add affiliation or ORCID only if supplied; absent fields remain blank.
- [ ] Insert actual public repository/release URLs in paper metadata and citation.
- [ ] Review named sharing of raw local paths and third-party licensing boundaries.
- [ ] Add the unchanged full historical artifact as a separate release asset if desired.
- [ ] Complete the current ReScience C submission form using the actual release URLs.

Building this repository does not publish it or submit the paper. The repository
URL and release identifier should be set only after the actual public release.
