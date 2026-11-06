#!/usr/bin/env python3
"""Resolve historical source pins without changing the frozen primary protocol.

The active release source differs only by two explicit newline insertions. The
original primary source remains archived byte-for-byte. Other source names are
never redirected. This module uses only the standard library and runs no code.
"""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACTIVE_NAME = "replication.cpp"
ARCHIVE_NAME = "frozen/replication.cpp"
REVISION_NAME = "release-source-revision.json"
ORIGINAL_SHA256 = "5ef65dfa3cf90a90490fe731949a0faafbbf77a6b777c3ccd6c34cf61ba54ef9"
EDITS = (
    (b"    for(const auto& v:j.array())out.push_back(v.number());return out;\n",
     b"    for(const auto& v:j.array())out.push_back(v.number());\n    return out;\n"),
    (b'    if(r.model=="SL")r.model="offline";if(r.model=="OSL")r.model="online";if(r.model=="DC")r.model="dirty";if(r.model=="BH")r.model="bh_analytic";\n',
     b'    if(r.model=="SL")r.model="offline";\n    if(r.model=="OSL")r.model="online";\n    if(r.model=="DC")r.model="dirty";\n    if(r.model=="BH")r.model="bh_analytic";\n'),
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checked_file(name):
    path = HERE / name
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(HERE):
        raise RuntimeError("Missing, symlinked or escaping release source: " + name)
    return path


def verify_release_source():
    """Verify the immutable source and exact limited release transformation."""
    path = checked_file(REVISION_NAME)
    revision = json.loads(path.read_text())
    if (revision.get("format_version") != 1 or revision.get("active_source") != ACTIVE_NAME
            or revision.get("historical_source") != ARCHIVE_NAME
            or revision.get("historical_source_sha256") != ORIGINAL_SHA256):
        raise RuntimeError("Unexpected release source revision identity")
    frozen = json.loads(checked_file("frozen_protocol.json").read_text())
    if frozen["source_sha256"][ACTIVE_NAME] != ORIGINAL_SHA256:
        raise RuntimeError("Original primary source pin differs")
    original = checked_file(ARCHIVE_NAME).read_bytes()
    active = checked_file(ACTIVE_NAME).read_bytes()
    if digest(original) != ORIGINAL_SHA256:
        raise RuntimeError("Archived primary source differs")
    expected = original
    for before, after in EDITS:
        if expected.count(before) != 1:
            raise RuntimeError("Release transformation does not apply exactly once")
        expected = expected.replace(before, after, 1)
    if active != expected or digest(active) != revision.get("active_source_sha256"):
        raise RuntimeError("Active source is not the exact documented formatting-only revision")
    # This is supplemental to the exact replacements above, not an unrestricted
    # whitespace-normalization test. Both edits split statements after semicolons,
    # outside strings/comments, so no C++ token or control flow is changed.
    if b"".join(original.split()) != b"".join(active.split()):
        raise RuntimeError("Non-whitespace source change")
    resolver_sha = digest(Path(__file__).read_bytes())
    if revision.get("resolver_sha256") != resolver_sha:
        raise RuntimeError("Release source resolver differs from the revision record")
    return {"revision_sha256": digest(path.read_bytes()),
            "resolver_sha256": resolver_sha,
            "active_source": ACTIVE_NAME, "active_source_sha256": digest(active),
            "historical_source": ARCHIVE_NAME, "historical_source_sha256": ORIGINAL_SHA256,
            "exact_documented_replacements_verified": True,
            "non_whitespace_bytes_identical": True}


def source_pin_path(name):
    """Return the file carrying a named frozen pin; redirect only the C++ source."""
    if name == ACTIVE_NAME:
        verify_release_source()
        return HERE / ARCHIVE_NAME
    return HERE / name


if __name__ == "__main__":
    print(json.dumps(verify_release_source(), indent=2))
