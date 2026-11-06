# Input provenance

The root `input_manifest.json` is the unchanged download/local SHA256 manifest.
Three source files reproduce an appended terminal newline; graph inputs are not normalized.
Brussels preserves released key-0 parallel-edge semantics; New York preserves every
DIMACS arc. Synthetic workloads, seeds, ordered adjacency hashes, and source splits
are frozen in `work/lapq/phase2/workload_manifest.json` and its protocol.

Read `THIRD_PARTY_NOTICES.md` for source/data attribution and license boundaries.
NY uses a moving mirror branch but accepts only the pinned bytes; a changed or
unavailable endpoint is a failed input retrieval, not permission to substitute data.
