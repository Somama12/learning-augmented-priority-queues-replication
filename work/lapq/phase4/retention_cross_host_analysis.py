#!/usr/bin/env python3
"""Read-only audit of imported retention evidence; executes no study/imported code."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
IMPORTED = HERE / 'imported'
RETENTION = IMPORTED / 'second_host_retention'
METHODS = ('occ_list_retain', 'occ_list_map', 'occ_linked_retain',
           'occ_linked_map', 'grouped_compact_reference')
MODES = ('enabled', 'disabled_during_trace')
LENGTHS = (1000, 10000, 100000)
MEMORY = ('python_traced_current_at_live_limit_bytes',
          'python_traced_peak_at_live_limit_bytes', 'python_traced_total_peak_bytes',
          'python_traced_empty_queue_alive_before_gc_bytes',
          'python_traced_empty_queue_alive_after_gc_bytes',
          'python_traced_after_queue_destroyed_bytes',
          'post_snapshot_stats_buffer_traced_increment_bytes')
AFTER = 'python_traced_empty_queue_alive_after_gc_bytes'
BEFORE = 'python_traced_empty_queue_alive_before_gc_bytes'
DESTROYED = 'python_traced_after_queue_destroyed_bytes'


def main():
    inputs, errors, warnings = {}, [], []

    def file_bytes(path):
        path = Path(path)
        data = path.read_bytes()
        inputs[path.relative_to(ROOT).as_posix()] = hashlib.sha256(data).hexdigest()
        return data

    def read(path):
        return json.loads(file_bytes(path).decode('utf-8-sig'))

    def digest(path):
        file_bytes(path)
        return inputs[Path(path).relative_to(ROOT).as_posix()]

    def check(value, message):
        if not value:
            errors.append(message)

    def stamp(value):
        return datetime.fromisoformat(value.replace(',', '.').replace('Z', '+00:00'))

    protocol_path = ROOT / 'work/lapq/phase3/ablation_protocol.json'
    manifest_path = ROOT / 'work/lapq/phase3/execution_manifest.json'
    protocol, manifest = read(protocol_path), read(manifest_path)
    plan_path = RETENTION / 'frozen_wrapper_plan.json'
    plan = read(plan_path)
    execution = read(RETENTION / 'retention_execution.json')
    jobs = [j for j in manifest['jobs'] if j['kind'] == 'retention']
    check(plan['jobs'] == jobs, 'Wrapper job list differs from local frozen retention jobs')
    check(plan['source_manifest_sha256'] == digest(manifest_path), 'Source execution manifest hash differs')
    check(manifest['protocol_sha256'] == digest(protocol_path), 'Frozen protocol hash differs')
    check(plan['source_hashes'] == manifest['source_sha256'], 'Wrapper source pins differ from execution manifest')
    check(execution['source_hashes'] == plan['source_hashes'], 'Execution source pins differ from wrapper plan')
    source_checks = []
    for relative, expected in plan['source_hashes'].items():
        path = (ROOT / relative).resolve()
        if not path.is_relative_to(ROOT):
            raise ValueError('Source path escapes workspace')
        actual = digest(path)
        source_checks.append({'file': relative, 'expected_sha256': expected,
                              'actual_sha256': actual, 'matched': actual == expected})
        check(actual == expected, 'Current pinned source/evidence mismatch: ' + relative)
    check(plan['worker_sha256'] == digest(ROOT / plan['worker']), 'Pinned worker hash differs')
    original_plan_path = IMPORTED / 'hardware_and_logs/second-host-retention-plan.json'
    original_plan_hash = digest(original_plan_path)
    check(execution['plan_sha256'] == original_plan_hash, 'Executed original wrapper plan hash differs')
    check(read(original_plan_path) == plan, 'Normalized snapshot changes original plan semantics')
    wrapper_hash = digest(RETENTION / 'second_host_retention.py')
    check(execution['wrapper_sha256'] == wrapper_hash, 'Executed wrapper hash differs')
    for relative in ('hardware_and_logs/second_host_retention.py', 'reproduction/second_host_retention.py'):
        check(digest(IMPORTED / relative) == wrapper_hash, 'Wrapper copy differs: ' + relative)
    for relative in ('hardware_and_logs/second-host-retention-plan.json', 'reproduction/second-host-retention-plan.json'):
        check(digest(IMPORTED / relative) == original_plan_hash, 'Original plan copy differs: ' + relative)
        check(read(IMPORTED / relative) == plan, 'Plan semantics differ: ' + relative)
    freeze = file_bytes(IMPORTED / 'hardware_and_logs/retention-wrapper-freeze.sha256').decode()
    check(wrapper_hash in freeze and original_plan_hash in freeze, 'Wrapper freeze ledger differs')
    plan_serialization = {
        'executed_original_sha256': original_plan_hash,
        'normalized_snapshot_sha256': digest(plan_path),
        'parsed_json_equal': read(original_plan_path) == plan,
        'original_crlf_count': file_bytes(original_plan_path).count(b'\r\n'),
        'snapshot_crlf_count': file_bytes(plan_path).count(b'\r\n'),
        'explanation': 'The wrapper verifies the original CRLF plan bytes and then writes a normalized LF JSON snapshot. Its byte hash differs; its parsed specifications are identical.'}
    design = protocol['bounded_live_diagnostic']
    check(tuple(design['methods']) == METHODS and tuple(design['insertions']) == LENGTHS,
          'Local retention methods/lengths differ from stated design')
    check(set(design['gc_modes']) == set(MODES) and design['max_live'] == 128,
          'Local retention GC/occupancy differs')
    expected_cells = Counter((m, n, g) for m in METHODS for n in LENGTHS for g in MODES)
    observed_cells = Counter((j['spec']['method'], j['spec']['insertions'], j['spec']['gc_mode']) for j in jobs)
    check(observed_cells == expected_cells, 'Planned cell coverage differs')
    check(len(execution['jobs']) == 30 and execution['expected_workers'] == 30,
          'Execution worker count differs')
    check(execution['status'] == 'passed' and execution.get('all_passed') is True,
          'Execution did not report success')
    check(set(p.parent.name for p in RETENTION.glob('*/result.json')) == set(j['job_id'] for j in jobs),
          'Observed result folders differ from planned jobs')
    rows, pids, chronological_gaps, host_environments = [], [], [], {'mac': [], 'windows_wsl2': []}
    retention_calendar_records, calendar_anomalies = [], []
    previous_finish = None

    def validate_result(data, spec, context):
        check(data.get('kind') == 'retention' and data.get('status') == 'ok', context + ': status/kind')
        check(data.get('spec') == spec, context + ': spec')
        check(data.get('all_extractions_correct') is True, context + ': extraction correctness')
        check(data.get('insertions') == data.get('extractions') == spec['insertions'], context + ': counts')
        for name in MEMORY:
            check(type(data.get(name)) is int and data[name] >= 0, context + ': invalid ' + name)
        stats = data['post_query_queue_stats']
        expected_nodes = spec['insertions'] if spec['method'].endswith('_retain') else 0
        check(stats['retained_node_map_entries'] == expected_nodes, context + ': node-map cardinality')
        check(stats['reachable_nodes'] == 0 and stats['live_entries'] == 0
              and stats['level0_cycle'] is False, context + ': live queue not empty')
        check(stats['index']['universe'] == 1 and stats['index']['size'] == 0,
              context + ': index universe/drain')
        if spec['method'] != 'grouped_compact_reference':
            check(stats.get('grouping') is False, context + ': unexpected occurrence grouping')
            check(stats.get('historical_node_cleanup') == spec['method'].split('_')[-1],
                  context + ': cleanup policy')
        check(type(data.get('explicit_collection_objects_while_queue_alive')) is int,
              context + ': missing explicit-GC count')

    for ordinal, job in enumerate(jobs):
        folder, spec = RETENTION / job['job_id'], job['spec']
        context = job['job_id']
        check(spec['max_live'] == 128 and spec['queue_seed'] == 101
              and spec['prediction'] == 'constant0' and spec['rank_universe'] == 1,
              context + ': trace contract differs')
        check(read(folder / 'spec.json') == spec, context + ': spec file differs')
        recorded = read(folder / 'execution.json')
        check(recorded == execution['jobs'][ordinal], context + ': report/individual execution differs')
        check(recorded['job_id'] == job['job_id'] and recorded['spec'] == spec
              and recorded['ordinal'] == ordinal, context + ': execution identity')
        check(recorded.get('passed') is True and recorded.get('returncode') == 0
              and not recorded.get('errors'), context + ': failed worker')
        check(recorded['source_hashes_before'] == recorded['source_hashes_after'] == plan['source_hashes'],
              context + ': before/after source guards differ')
        for stream in ('stdout', 'stderr'):
            check(digest(folder / (stream + '.txt')) == recorded[stream + '_sha256'], context + ': stream hash')
        second = read(folder / 'result.json')
        check(read(folder / 'stdout.txt') == second, context + ': stdout/result differ')
        first_path = ROOT / 'work/lapq/phase3/results' / job['output_name']
        first = read(first_path)
        check(first['execution_manifest_sha256'] == digest(manifest_path), context + ': first-host manifest pin')
        check(first['source_sha256_at_launch'] == first['source_sha256_at_completion'] == plan['source_hashes'],
              context + ': first-host source pins differ')
        for label, result in (('mac', first), ('windows_wsl2', second)):
            validate_result(result, spec, label + '/' + context)
            env = result['environment']
            host_environments[label].append({k: env[k] for k in
                ('python', 'platform', 'machine', 'cpu_count_visible', 'numpy', 'sortedcontainers', 'gc_thresholds')})
        check(first['post_query_queue_stats']['max_skiplist_height'] == second['post_query_queue_stats']['max_skiplist_height'],
              context + ': cross-host sampled maximum height differs')
        pids.append(second['environment']['process_id'])
        start, finish = stamp(recorded['started_utc']), stamp(recorded['finished_utc'])
        calendar_record = {'job_id': context, 'ordinal': ordinal,
                           'started_utc': recorded['started_utc'], 'finished_utc': recorded['finished_utc'],
                           'calendar_difference_seconds': (finish-start).total_seconds()}
        retention_calendar_records.append(calendar_record)
        if start > finish:
            calendar_anomalies.append(calendar_record)
            warnings.append(context + ': reversed calendar timestamps; no retention duration is inferred')
        if previous_finish is not None:
            gap = (start - previous_finish).total_seconds()
            chronological_gaps.append(gap)
            if gap < 0:
                warnings.append(context + ': negative inter-worker calendar gap; ordering relies on blocking calls and ordinals')
        previous_finish = finish
        rows.append({'job_id': context, 'method': spec['method'], 'insertions': spec['insertions'],
                     'gc_mode': spec['gc_mode'], 'spec': spec,
                     'mac': {k: first[k] for k in MEMORY},
                     'windows_wsl2': {k: second[k] for k in MEMORY},
                     'mac_retained_node_map_entries': first['post_query_queue_stats']['retained_node_map_entries'],
                     'windows_wsl2_retained_node_map_entries': second['post_query_queue_stats']['retained_node_map_entries'],
                     'mac_explicit_gc_collected': first['explicit_collection_objects_while_queue_alive'],
                     'windows_wsl2_explicit_gc_collected': second['explicit_collection_objects_while_queue_alive'],
                     'maximum_sampled_height_both_hosts': second['post_query_queue_stats']['max_skiplist_height'],
                     'second_over_first_post_gc_bytes': second[AFTER] / first[AFTER],
                     'second_minus_first_post_gc_bytes': second[AFTER] - first[AFTER],
                     'first_host_file': first_path.relative_to(ROOT).as_posix(),
                     'second_host_file': (folder / 'result.json').relative_to(ROOT).as_posix()})
    check(len(set(pids)) == 30, 'Retention PID evidence does not distinguish 30 fresh workers')
    by_cell = {(r['method'], r['insertions'], r['gc_mode']): r for r in rows}
    comparisons = []
    for n in LENGTHS:
        for mode in MODES:
            for bucket in ('list', 'linked'):
                retained, cleaned = by_cell[(f'occ_{bucket}_retain', n, mode)], by_cell[(f'occ_{bucket}_map', n, mode)]
                comparisons.append({'insertions': n, 'gc_mode': mode, 'bucket': bucket,
                    **{host + '_post_gc_retain_over_map': retained[host][AFTER]/cleaned[host][AFTER]
                       for host in ('mac', 'windows_wsl2')}})
    memory_diagnostics = {}
    for host in ('mac', 'windows_wsl2'):
        mode_diffs = {field: max(abs(by_cell[(m, n, 'enabled')][host][field]
                                   - by_cell[(m, n, 'disabled_during_trace')][host][field])
                                for m in METHODS for n in LENGTHS) for field in MEMORY}
        memory_diagnostics[host] = {
            'explicit_gc_collected_counts': sorted(set(r[host + '_explicit_gc_collected'] for r in rows)),
            'before_minus_after_gc_byte_range': [min(r[host][BEFORE]-r[host][AFTER] for r in rows),
                                                max(r[host][BEFORE]-r[host][AFTER] for r in rows)],
            'after_queue_destruction_bytes_range': [min(r[host][DESTROYED] for r in rows), max(r[host][DESTROYED] for r in rows)],
            'enabled_vs_disabled_max_absolute_differences_bytes': mode_diffs,
            'map_only_post_gc_bytes_range': [min(r[host][AFTER] for r in rows if r['method']=='occ_list_map'),
                                            max(r[host][AFTER] for r in rows if r['method']=='occ_list_map')]}

    # Hardware and chronology: parse records, never execute imported capture scripts.
    logs = IMPORTED / 'hardware_and_logs'
    windows_before = read(logs / 'windows-before-native.json')
    windows_after = read(logs / 'windows-after-measurements.json')
    first_host_protocol = read(ROOT / 'work/lapq/phase2/primary_small_protocol.json')
    linux = [read(logs / name) for name in ('linux-before-native.json', 'linux-after-native.json', 'linux-after-retention.json')]
    def proc_kib(text, field):
        return int(next(line.split()[1] for line in text.splitlines() if line.startswith(field + ':'))) * 1024
    initial = linux[0]
    lscpu = next(c['stdout'] for c in initial['commands'] if c['argv'] == ['lscpu'])
    def cpu_field(label):
        return next(line.split(':', 1)[1].strip() for line in lscpu.splitlines() if line.startswith(label + ':'))
    build = read(IMPORTED / 'second_host_native/build/build.json')
    native = read(IMPORTED / 'second_host_native/native_experiment.json')
    native_gaps = []
    last_finish = None
    for index, case in enumerate(native['cases']):
        process = case['process']
        check(case['execution_ordinal'] == index, 'Native execution ordinal differs')
        start, finish = stamp(process['started_at_utc']), stamp(process['finished_at_utc'])
        check(start <= finish, 'Native lifecycle reversed')
        if last_finish is not None:
            native_gaps.append((start-last_finish).total_seconds())
        last_finish = finish
    check(all(g >= 0 for g in native_gaps), 'Native recorded case intervals overlap')
    check(stamp(native['finished_at_utc']) <= stamp(execution['started_utc']), 'Native/retention stage intervals overlap')
    build_names = ('build.json', 'selftest.json', 'sanitizer_build.json', 'sanitizer_selftest.json')
    preparations = [read(IMPORTED / 'second_host_native/build' / n) for n in build_names]
    check(all(stamp(p['finished_at_utc']) <= stamp(native['started_at_utc']) for p in preparations),
          'Recorded preparation extends into native timing stage')
    handshake = read(logs / 'clock-handshake-after.json')
    linux_clock = stamp(handshake['linux_utc'])
    offset_bracket = [(stamp(handshake[k])-linux_clock).total_seconds()
                      for k in ('windows_before_utc', 'windows_after_utc')]
    awake = read(logs / 'native-awake-request.json')
    power_plan = file_bytes(logs / 'power-plan-after.txt').decode('utf-8-sig').strip()
    pip_freeze = file_bytes(logs / 'logs/pip-freeze.txt').decode('utf-8-sig')
    for relative in ('reproduction/run-native-stages.sh', 'reproduction/run-retention-stages.sh',
                     'reproduction/run-native-awake.ps1', 'reproduction/capture-windows-host.ps1',
                     'reproduction/linux-host-metadata.py'):
        file_bytes(IMPORTED / relative)
    output = {
        'schema_version': 1, 'analysis_kind': 'Read-only independent retention/hardware audit of supplied second-host evidence',
        'imported_code_executed': False, 'new_benchmarks_executed': False,
        'audit_passed': not errors, 'errors': errors, 'warnings': warnings,
        'coverage': {'expected_cells': 30, 'cross_host_matched_cells': len(rows),
                     'methods': list(METHODS), 'insertions': list(LENGTHS), 'gc_modes': list(MODES),
                     'max_live': 128, 'queue_seed': 101, 'rank_universe': 1, 'prediction': 'constant0',
                     'distinct_second_host_pids': len(set(pids)), 'pinned_source_evidence_files': len(source_checks)},
        'source_hash_checks': source_checks,
        'wrapper_review': {
            'sha256': wrapper_hash,
            'plan_serialization': plan_serialization,
            'behavior': 'Selects the exact 30 existing retention jobs; verifies their source manifest and sources; invokes unchanged worker_main in blocking subprocess.run calls; preserves stdout/stderr/return codes, exact specs and before/after source pins.',
            'no_pointer_detachment_scope': 'The four occurrence arms only remove exhausted map entries and/or replace occurrence rank buckets; no skip-list pointer detachment. The grouped compact reference retains its existing combined detachment policy.',
            'validation_scope': plan['validation_provenance'],
            'limits': 'Source equality and raw-record consistency are verified; this audit does not observe historical execution directly or prove exclusion of all background processes.'},
        'hardware': {
            'first_host_recorded_hardware': first_host_protocol['environment']['hardware_verified_by_coordinator'],
            'physical_host_evidence': 'User-identified second physical laptop, supported by distinct Windows/WSL hardware records; not merely a container on the original Mac.',
            'manufacturer': windows_before['manufacturer'], 'model': windows_before['model'],
            'cpu': windows_before['cpu'], 'physical_cores_reported': int(cpu_field('Core(s) per socket')) * int(cpu_field('Socket(s)')),
            'threads_per_core_reported': int(cpu_field('Thread(s) per core')),
            'installed_ram_bytes': windows_before['installed_ram_bytes'],
            'windows_display_version': windows_before['windows_display_version'],
            'windows_build': str(windows_before['windows_build']) + '.' + str(windows_before['windows_ubr']),
            'windows_legacy_registry_name': windows_before['legacy_registry_product_name'],
            'windows_name_note': 'Legacy registry ProductName says Windows 10 Pro; build/display metadata and handoff identify Windows 11 Pro 25H2. Retain the discrepancy instead of silently treating ProductName as current branding.',
            'wsl_distribution': initial['distro_environment'], 'linux_os_release': initial['os_release'],
            'linux_kernel': initial['uname'][2], 'machine': initial['uname'][4],
            'visible_cpu_count': initial['visible_cpu_count'], 'allowed_cpu_affinity': initial['affinity'],
            'wsl_visible_memory_bytes': proc_kib(initial['meminfo'], 'MemTotal'),
            'wsl_swap_bytes': proc_kib(initial['meminfo'], 'SwapTotal'),
            'swap_used_at_snapshots_bytes': [proc_kib(d['meminfo'], 'SwapTotal')-proc_kib(d['meminfo'], 'SwapFree') for d in linux],
            'cgroup_limits': initial['cgroup_limits'], 'resource_limits': initial['resource_limits'],
            'python': initial['python'], 'compiler': build['compiler_version'].splitlines()[0],
            'native_compile_flags': build['compile_flags'],
            'package_versions': [line for line in pip_freeze.splitlines() if line.lower().startswith(('numpy==','networkx==','sortedcontainers=='))],
            'host_environments_in_retention_records': {h: [json.loads(s) for s in sorted(set(json.dumps(e,sort_keys=True) for e in envs))]
                                                      for h, envs in host_environments.items()},
            'power': {'ac_line_status_before_after': [windows_before['ac_line_status'], windows_after['ac_line_status']],
                      'battery_saver_before_after': [windows_before['battery_saver'], windows_after['battery_saver']],
                      'plan_observed_after': power_plan, 'native_awake_request': awake},
            'limits': ['Windows/WSL virtualization, CPython version and compiler differ from Mac, so host effects are not isolated hardware effects.',
                       'No CPU pinning, frequency/thermal control, continuous power trace or exclusive-machine guarantee.',
                       'No cgroup CPU/memory quota observed; WSL still exposes only its VM-visible memory, not all installed RAM.',
                       'Zero swap use at snapshots does not prove no transient paging during measurements.']},
        'chronology': {
            'native_cases': len(native['cases']), 'native_case_intervals_nonoverlapping': all(g >= 0 for g in native_gaps),
            'native_first_start_utc': native['started_at_utc'], 'native_last_finish_utc': native['finished_at_utc'],
            'retention_first_start_utc': execution['started_utc'], 'retention_last_finish_utc': execution['finished_utc'],
            'retention_calendar_lifecycles_monotone': not calendar_anomalies and all(g >= 0 for g in chronological_gaps),
            'retention_inter_worker_calendar_gaps_nonnegative': all(g >= 0 for g in chronological_gaps),
            'retention_calendar_anomalies': calendar_anomalies,
            'retention_calendar_records': retention_calendar_records,
            'minimum_between_retention_workers_seconds': min(chronological_gaps),
            'native_finished_before_retention_seconds': (stamp(execution['started_utc'])-stamp(native['finished_at_utc'])).total_seconds(),
            'all_recorded_compilation_selftests_finished_before_native': all(stamp(p['finished_at_utc']) <= stamp(native['started_at_utc']) for p in preparations),
            'windows_minus_linux_calendar_bracket_seconds': offset_bracket,
            'interpretation': 'Stage shell scripts and wrappers use blocking calls and record consecutive ordinals. Native lifecycle timestamps are internally ordered. One retention worker has reversed calendar labels, consistent with a calendar-clock discontinuity; retention ordering therefore rests on blocking wrapper calls and ordinal/record consistency, not UTC durations. The post-run handshake also shows a Windows/WSL calendar offset. Algorithm duration clocks are monotonic/process clocks; retention measurements are allocation snapshots, not duration benchmarks.',
            'limits': 'The records substantiate ordering of these study jobs, not absence of all background OS/user processes. Calendar lifecycle times are not algorithm runtimes.'},
        'rows': sorted(rows, key=lambda r:(r['insertions'],r['gc_mode'],r['method'])),
        'within_host_cleanup_comparisons': comparisons,
        'memory_diagnostics': memory_diagnostics,
        'interpretation': [
            'This is a second-physical-host repetition of the 30 bounded-live Python retention cells, not a rerun of Python graph timings or graph-memory ablations.',
            'The history-retaining maps contain N entries after drain; map-cleaned and compact maps contain zero. Both hosts return every expected key and have no live level-zero entries after drain.',
            'Report bytes and within-host ratios descriptively; each method/length/GC cell has one process, not repeated samples supporting a confidence interval.',
            'Absolute cross-host allocation differences cannot be assigned to hardware: CPython 3.13 versus 3.12, OS, allocator and runtime layout also differ.',
            'Extracted skip-list node pointers are not detached in the four occurrence arms; the compact reference is explicitly coupled. Linked occurrence-bucket entries have their own separate link-removal bookkeeping.',
            'Zero explicit-GC collected objects means before/after-GC byte differences are not evidence of reclaimed cycles; interpreter free-list effects can also change traced bytes.',
            'The first-host phase-three full-workload trace gate is preserved evidence, not newly executed second-host validation; each new retention worker checks every expected extraction.',
            'No constant-space theorem, process-RSS reduction, general predictor benefit, or cross-host Python query-time result follows from these retention measurements.'],
        'input_sha256': inputs,
        'analysis_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    destination = HERE / 'retention_cross_host_summary.json'
    destination.write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps({'audit_passed': output['audit_passed'], 'matched_cells': len(rows),
                      'errors': errors, 'summary': str(destination)}))
    if errors:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
