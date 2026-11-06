#!/usr/bin/env python3
"""Audit frozen coverage and compare within-host paired ratios after measurement."""
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics

ROOT = Path('/root/research/learned-pq-second-host')
OUT = ROOT / 'second_host_evidence/comparison'
OUT.mkdir(exist_ok=False)
OLD = ROOT / 'work/lapq/phase2/native_results'
NEW = ROOT / 'work/lapq/phase2/second_host_native'
RET = ROOT / 'work/lapq/phase3/second_host_retention'
PLAN = json.loads((ROOT / 'second_host_evidence/second-host-retention-plan.json').read_text(encoding='utf-8-sig'))


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2)+'\n')


def csvwrite(name, rows):
    with (OUT / name).open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


old, new = read(OLD/'native_experiment.json'), read(NEW/'native_experiment.json')
summaries = {'Mac': read(OLD/'native_summary.json'), 'Windows_WSL2': read(NEW/'native_summary.json')}
documents = {'Mac': old, 'Windows_WSL2': new}
immutable_job_keys = ('case_id', 'workload', 'training_graph', 'train_source', 'train_index',
                      'query_source', 'source_group', 'paired_seed', 'methods', 'repetitions',
                      'expected_typed_distance_sha256', 'expected_reachable',
                      'target_adjacency_sha256', 'oracle_binary64_sha256', 'oracle_binary64_bytes')
assert len(old['protocol']['jobs']) == len(new['protocol']['jobs']) == 42
for a, b in zip(old['protocol']['jobs'], new['protocol']['jobs']):
    assert all(a[k] == b[k] for k in immutable_job_keys), a['case_id']
for key in ('protocol_seed', 'selected_workloads', 'repetitions', 'compile_flags',
            'workload_manifest_sha256', 'workload_generator_sha256', 'runner_sha256'):
    assert old['protocol'][key] == new['protocol'][key], key
for name, item in old['protocol']['exported_inputs'].items():
    for key in ('text_sha256', 'adjacency_sha256', 'n', 'm'):
        assert item[key] == new['protocol']['exported_inputs'][name][key], (name, key)
assert old['build']['source_sha256'] == new['build']['source_sha256']
assert new['build']['compile_flags'] == ['-O3', '-std=c++17', '-DNDEBUG']
assert new['all_passed'] is True and not new['failures']
assert summaries['Windows_WSL2']['coverage']['complete_and_matching'] is True

ratios, height_seeds, orders = {}, {}, {}
for host, directory in (('Mac', OLD), ('Windows_WSL2', NEW)):
    doc, summary = documents[host], summaries[host]
    assert len(doc['cases']) == 42 and doc['all_passed']
    assert len({c['case']['case_id'] for c in doc['cases']}) == 42
    source_values, height_seeds[host], orders[host] = {}, {}, {}
    timing_count = 0
    for case in doc['cases']:
        job = case['case']
        assert case['passed'] and not case['errors'] and case['distance_verification']['passed']
        csvpath = directory/'cases'/job['case_id']/'native.csv'
        assert sha(csvpath) == case['timing_csv_sha256']
        rows = list(csv.DictReader(csvpath.open()))
        timing_count += len(rows)
        assert len(rows) == len(job['methods'])*7
        index = {(row['method'], int(row['repetition'])): row for row in rows}
        assert len(index) == len(rows)
        for row in rows:
            assert row['correct'] == row['drained'] == 'true'
            k = (job['case_id'], row['method'], int(row['repetition']))
            height_seeds[host][k] = row['height_seed']
            orders[host][k] = row['order']
        for baseline in ('direct_heap', 'std_heap'):
            for method in job['methods']:
                for clock in ('wall', 'cpu'):
                    paired = [float(index[(method, rep)][clock+'_seconds']) /
                              float(index[(baseline, rep)][clock+'_seconds']) for rep in range(7)]
                    source_values.setdefault((job['workload'], method, baseline, clock), []).append(statistics.median(paired))
    assert timing_count == 3444 == summary['raw_native_timing_records_seen']
    ratios[host] = {key: statistics.median(values) for key, values in source_values.items()}
    for (workload, method, baseline, clock), value in ratios[host].items():
        recorded = summary['workloads'][workload]['methods'].get(method, {}).get(baseline)
        if recorded is not None:
            expected = recorded['source_median_'+clock+'_ratio']
            assert abs(value-expected) < 1e-12, (host, workload, method, baseline, clock)
assert height_seeds['Mac'] == height_seeds['Windows_WSL2']

ratio_rows = []
ranking_rows, winners = [], []
workloads = new['protocol']['selected_workloads']
for workload in workloads:
    methods = sorted({key[1] for key in ratios['Mac'] if key[0] == workload})
    for method in methods:
        row = {'workload': workload, 'method': method}
        for baseline in ('direct_heap', 'std_heap'):
            for clock in ('wall', 'cpu'):
                for host in ('Mac', 'Windows_WSL2'):
                    row[f'{host}_{clock}_ratio_to_{baseline}'] = ratios[host][workload, method, baseline, clock]
        ratio_rows.append(row)
    ranked = {host: sorted(methods, key=lambda m: (ratios[host][workload, m, 'direct_heap', 'wall'], m))
              for host in ('Mac', 'Windows_WSL2')}
    winner = {'workload': workload, 'Mac_fastest': ranked['Mac'][0], 'Windows_WSL2_fastest': ranked['Windows_WSL2'][0],
              'fastest_changed': ranked['Mac'][0] != ranked['Windows_WSL2'][0]}
    winners.append(winner)
    for method in methods:
        a, b = ranked['Mac'].index(method)+1, ranked['Windows_WSL2'].index(method)+1
        ranking_rows.append({'workload': workload, 'method': method, 'Mac_rank': a, 'Windows_WSL2_rank': b, 'rank_changed': a != b})
csvwrite('native_paired_ratios_side_by_side.csv', ratio_rows)
csvwrite('native_rankings_side_by_side.csv', ranking_rows)
csvwrite('native_fastest_methods.csv', winners)

execution = read(RET/'retention_execution.json')
assert execution['all_passed'] and len(execution['jobs']) == 30
retention_rows = []
retention_results = {}
for job in PLAN['jobs']:
    pair = {'Mac': read(ROOT/'work/lapq/phase3/results'/job['output_name']),
            'Windows_WSL2': read(RET/job['job_id']/'result.json')}
    for host, record in pair.items():
        assert record['status'] == 'ok' and record['spec'] == job['spec']
        assert record['all_extractions_correct'] and record['extractions'] == job['spec']['insertions']
        retention_results[host, job['spec']['method'], job['spec']['gc_mode'], job['spec']['insertions']] = record
    row = {'method': job['spec']['method'], 'gc_mode': job['spec']['gc_mode'], 'insertions': job['spec']['insertions']}
    for host, record in pair.items():
        for snapshot in ('before_gc', 'after_gc'):
            row[f'{host}_empty_queue_{snapshot}_bytes'] = record['python_traced_empty_queue_alive_'+snapshot+'_bytes']
        row[host+'_retained_node_map_entries'] = record['post_query_queue_stats'].get('retained_node_map_entries')
    retention_rows.append(row)
retention_rows.sort(key=lambda r: (r['method'], r['gc_mode'], r['insertions']))
csvwrite('retention_side_by_side.csv', retention_rows)

preservation = {}
for layer_name, checksums in (('outer', read(ROOT/'completed-study-manifest.json')['files']),
                              ('base', read(ROOT/'artifact_checksums.json'))):
    for name, checksum in checksums.items():
        assert sha(ROOT/name) == checksum, name
    preservation[layer_name+'_members_verified'] = len(checksums)
write(OUT/'original_evidence_preservation.json', {'status': 'passed', **preservation})

report = {'status': 'passed', 'created_utc': datetime.now(timezone.utc).isoformat(),
          'native_cases_per_host': 42, 'native_timings_per_host': 3444,
          'retention_workers_per_host': 30, 'failures_second_host': [],
          'protocol_job_inputs_hashes_and_case_order_match': True,
          'all_method_repetition_height_seeds_match_across_hosts': True,
          'method_repetition_order_cells_differing_across_hosts': sum(orders['Mac'][k] != orders['Windows_WSL2'][k] for k in orders['Mac']),
          'ratio_audit': 'Independently recomputed each within-repetition method/baseline ratio from raw CSVs, then median across seven repetitions per source and six source units per workload. Recomputed values agree with shipped/second-host summary values.',
          'ranking_definition': 'Sort each method by its median source-level paired wall ratio to direct_heap, which is assigned its self-ratio 1.0. Descriptive ranks without significance threshold.',
          'fastest_by_workload': winners,
          'changed_rank_positions': sum(row['rank_changed'] for row in ranking_rows),
          'rank_position_count': len(ranking_rows), 'preservation': preservation,
          'first_host_compiler': old['build']['compiler_version'],
          'second_host_compiler': new['build']['compiler_version'],
          'interpretation': ['This is Windows + WSL2 evidence from a second physical laptop identified by the user.',
                             'Both hosts use the same workload/source cases, seven repetitions and matched height seeds. OS/compiler/standard-library differences may change seeded method permutations; actual orders are preserved.',
                             'Hardware, OS, compiler and Python interpreter differ together; cross-host differences cannot be assigned exclusively to hardware.',
                             'Python traces measure ownership and traced allocations with at most 128 live entries, not runtime or RSS; empty snapshots occur before stats allocation.',
                             'Only three training sources per workload and one near/far query per source; rankings describe these fixed instances.',
                             'The shipped phase-three gate remains historical first-host validation; each second-host retention worker checks every extraction and source hash.',
                             'Windows/WSL calendar timestamps differ by about 45 seconds at the post-run handshake. Stage order was enforced synchronously and durations use monotonic/process clocks.']}
write(OUT/'comparison_audit.json', report)

lines = ['SECOND PHYSICAL HOST — COMPLETED RESULTS', '',
         'Windows 11 Pro 25H2 build 26200.9168 + WSL2, Ubuntu 24.04.',
         'Dell Latitude 5400, Intel i5-8365U (4 cores/8 threads), 8 GiB installed RAM.',
         'WSL visible memory about 3.7 GiB with 1 GiB swap, eight logical CPUs.',
         'Python 3.12.3; NumPy 2.2.4; NetworkX 3.4.2; SortedContainers 2.4.0.',
         'g++ 13.3.0; C++17 -O3 -DNDEBUG; no fast-math.',
         'Original Mac: Apple M1 MacBook Air, 8 GiB, Python 3.13.3; compiler in audit JSON.', '',
         'All 42 C++ cases and 3,444 method timings passed; zero failures/timeouts.',
         'Normal and ASAN/UBSAN selftests passed (641,147 queue extractions and',
         '3,680 graph-method pairs each). All 30 Python retention workers passed.',
         'Python index/queue selfchecks passed before timing. All original 479 outer',
         'and 1,315 base manifest entries still match their archived hashes.',
         'Timeouts remained 180 seconds per native case and 1,800 per retention worker.', '',
         'Native comparison: paired wall ratios to direct_heap and std_heap are',
         'computed inside each repetition, then collapsed per source and workload.',
         'Full wall/CPU ratios and ranks are in comparison/*.csv. Ratio <1 is faster.', '',
         'Fastest native method by median paired wall ratio to direct_heap:',
         'Workload | Mac | Windows + WSL2']
for winner in winners:
    lines.append(f"{winner['workload']} | {winner['Mac_fastest']} | {winner['Windows_WSL2_fastest']}")
lines += ['', f"Changed fastest method on {sum(w['fastest_changed'] for w in winners)} of {len(winners)} workloads;",
          f"{report['changed_rank_positions']} of {report['rank_position_count']} method rank positions changed.",
          'These are descriptive rankings for the fixed cases, not significance claims.', '',
          'Empty queue alive AFTER full GC at 100,000 insertions (bytes; GC enabled):',
          'Method | Mac bytes | Windows + WSL2 bytes | retained nodes (Windows)']
for method in PLAN['methods']:
    row = next(r for r in retention_rows if r['method'] == method and r['gc_mode'] == 'enabled' and r['insertions'] == 100000)
    lines.append(f"{method} | {row['Mac_empty_queue_after_gc_bytes']} | {row['Windows_WSL2_empty_queue_after_gc_bytes']} | {row['Windows_WSL2_retained_node_map_entries']}")
lines += ['', 'Retention traces preserve unique increasing keys, rank0, <=128 live,',
          '1k/10k/100k insertions, seed101, fresh processes and both GC modes.',
          'They repeat occurrence-history, map-only cleanup, linked/list buckets and',
          'the grouped compact reference. All occurrence arms retain original',
          'insert/extract semantics; map cleanup does not detach node pointers.',
          'Cross-host absolute bytes also reflect Python 3.13 versus 3.12.', '',
          'Runtime: AC power before and after, Balanced plan, no battery saver.',
          'A temporary system-awake request covered C++ stages. No concurrent',
          'study timing, compilation, tracing or analysis. CPU affinity, frequency,',
          'thermals and background OS activity were uncontrolled. Windows/WSL wall',
          'calendar clocks differed by about 45 s; durations used monotonic clocks.', '',
          'Only the new second_host_native and second_host_retention destinations',
          'received experiment results. Original sources, manifests, gate/preflight',
          'records and first-host measurements remain intact.', '',
          'The Windows laptop session is complete. Return to the Mac with the ZIP.']
(ROOT/'second_host_evidence/RESULTS-SUMMARY.txt').write_text('\n'.join(lines)+'\n')
print(json.dumps({'audit_passed': True, 'fastest_by_workload': winners,
                  'rank_positions_changed': report['changed_rank_positions'],
                  'retention_100k_gc_enabled': [r for r in retention_rows if r['insertions']==100000 and r['gc_mode']=='enabled']}))
