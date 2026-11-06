#!/usr/bin/env bash
set -euo pipefail
workspace=/mnt/c/Users/somam/Documents/Codex/2026-11-06/read-windows-second-machine-handoff-txt
study=/root/research/learned-pq-second-host
python3 "$workspace/work/prepare-linux-study.py" "$workspace/work/transfer-original/learned-priority-queues-working-paper-and-ablations.zip" "$study"
cd "$study"
cp "$workspace/work/second_host_retention.py" second_host_evidence/
cp "$workspace/work/second-host-retention-plan.json" second_host_evidence/
cp "$workspace/work/windows-host-initial.json" "$workspace/work/wsl-setup-reboot-required.json" second_host_evidence/
python3 verify_artifact.py | tee second_host_evidence/logs/verify_artifact_before.txt
export DEBIAN_FRONTEND=noninteractive
apt-get update > second_host_evidence/logs/apt-update.txt 2>&1
apt-get install -y build-essential python3-venv unzip > second_host_evidence/logs/apt-install.txt 2>&1
dpkg-query -W build-essential gcc g++ python3 python3-venv unzip > second_host_evidence/logs/debian-package-versions.txt
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt > second_host_evidence/logs/pip-install.txt 2>&1
.venv/bin/python -m pip freeze > second_host_evidence/logs/pip-freeze.txt
.venv/bin/python fetch_inputs.py > second_host_evidence/logs/fetch-inputs.txt 2>&1
.venv/bin/python fetch_inputs.py --check-only > second_host_evidence/logs/check-inputs.txt 2>&1
.venv/bin/python work/lapq/phase2/native_experiment.py --help > second_host_evidence/logs/native-cli-help.txt
.venv/bin/python work/lapq/phase2/native_summary.py --help > second_host_evidence/logs/native-summary-cli-help.txt
.venv/bin/python -m py_compile second_host_evidence/second_host_retention.py
sha256sum second_host_evidence/second_host_retention.py second_host_evidence/second-host-retention-plan.json > second_host_evidence/retention-wrapper-freeze.sha256
.venv/bin/python work/lapq/phase2/test_indexes.py > second_host_evidence/logs/test-indexes.txt 2>&1
.venv/bin/python work/lapq/phase2/test_veb_radix.py > second_host_evidence/logs/test-veb-radix.txt 2>&1
.venv/bin/python work/lapq/phase2/benchmark.py selfcheck > second_host_evidence/logs/phase2-selfcheck.txt 2>&1
printf 'Bootstrap, pinned inputs, and Python validation completed.\n'
