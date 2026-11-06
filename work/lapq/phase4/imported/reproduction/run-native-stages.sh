#!/usr/bin/env bash
set -euo pipefail
cd /root/research/learned-pq-second-host
logs=second_host_evidence/logs
native=work/lapq/phase2/second_host_native
cp /mnt/c/Users/somam/Documents/Codex/2026-11-06/read-windows-second-machine-handoff-txt/work/linux-host-metadata.py second_host_evidence/
.venv/bin/python second_host_evidence/linux-host-metadata.py second_host_evidence/linux-before-native.json
.venv/bin/python work/lapq/phase2/native_experiment.py --stage plan --destination "$native" 2>&1 | tee "$logs/native-plan.txt"
.venv/bin/python work/lapq/phase2/native_experiment.py --stage prepare --destination "$native" --compiler g++ --sanitizers 2>&1 | tee "$logs/native-prepare.txt"
.venv/bin/python work/lapq/phase2/native_experiment.py --stage run --destination "$native" --timeout 180 2>&1 | tee "$logs/native-run.txt"
.venv/bin/python second_host_evidence/linux-host-metadata.py second_host_evidence/linux-after-native.json
.venv/bin/python work/lapq/phase2/native_summary.py --input "$native/native_experiment.json" --output "$native/native_summary.json" 2>&1 | tee "$logs/native-summary.txt"
