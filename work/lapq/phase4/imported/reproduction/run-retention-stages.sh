#!/usr/bin/env bash
set -euo pipefail
cd /root/research/learned-pq-second-host
.venv/bin/python -c 'import json; p="work/lapq/phase2/second_host_native/native_summary.json"; s=json.load(open(p)); assert s["coverage"]["complete_and_matching"] and s["raw_native_timing_records_seen"]==3444; print(json.dumps({"native_cases":s["coverage"]["observed_cases"],"timings":s["raw_native_timing_records_seen"],"coverage_passed":True}))'
sha256sum -c second_host_evidence/retention-wrapper-freeze.sha256
.venv/bin/python second_host_evidence/second_host_retention.py --root . --plan second_host_evidence/second-host-retention-plan.json 2>&1 | tee second_host_evidence/logs/retention-run.txt
.venv/bin/python second_host_evidence/linux-host-metadata.py second_host_evidence/linux-after-retention.json
.venv/bin/python verify_artifact.py | tee second_host_evidence/logs/verify_artifact_after.txt
