# Licensing scope and third-party notices

The MIT license covers new software/documentation authored for this audit by
Sahil Siddiqui using Codex. It is not a license for the original Benomar–Coester
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
