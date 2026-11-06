"""Recompute paired native ratios from raw CSVs; never execute imported code.

Run: python3 work/lapq/phase4/native_cross_host_analysis.py
All clock ratios are formed within a host, case and repetition before collapsing
seven repetitions to a source median, then six sources to a workload median.
Hosts are never pooled. Bootstrap intervals condition on the fixed graph and
three training-source clusters, preserving each cluster's near/far strata.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import random
import statistics as st

HERE = Path(__file__).resolve().parent
CLOCKS = ('cpu', 'wall')
ADVISORS = ('exact', 'linear', 'hist64')
ABLATIONS = [(f'guided_bitmap_{a}', f'guided_set_{a}') for a in ADVISORS]
ABLATIONS += [(f'guided_{index}_{a}', 'plain_skiplist')
              for index in ('set', 'bitmap') for a in ADVISORS]
ABLATIONS += [('grouped_heap', 'direct_heap'), ('grouped_heap', 'std_heap'),
              ('std_heap', 'direct_heap')]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def read_csv(path):
    with Path(path).open(newline='') as stream:
        return list(csv.DictReader(stream))


def check(ok, message, errors):
    if not ok:
        errors.append(message)


def load_host(root):
    document = read_json(root / 'native_experiment.json')
    plan = document['protocol']
    jobs = {j['case_id']: j for j in plan['jobs']}
    records = {r['case']['case_id']: r for r in document['cases']}
    errors = []
    check(len(jobs) == len(plan['jobs']) == 42, 'expected exactly 42 unique planned cases', errors)
    check(len(records) == len(document['cases']) == 42, 'expected exactly 42 unique observed cases', errors)
    check(set(records) == set(jobs), 'observed and planned cases differ', errors)
    check(document.get('all_passed') is True and not document.get('failures'), 'top-level failure', errors)
    check(read_json(root / 'native_protocol.json') == plan, 'protocol copies differ', errors)
    check({p.name for p in (root / 'cases').iterdir() if p.is_dir()} == set(jobs), 'case directory coverage differs', errors)
    raw, case_audits, provenance, timing_ratios = {}, [], {}, []
    for case_id, job in jobs.items():
        rec = records[case_id]
        case_dir = root / 'cases' / case_id
        prefix = case_id + ': '
        check(rec['case'] == job, prefix + 'job mismatch', errors)
        check(read_json(case_dir / 'case_record.json') == rec, prefix + 'case record copies differ', errors)
        meta = read_json(case_dir / 'native.json')
        check(meta == rec['native_metadata'], prefix + 'metadata copies differ', errors)
        check(rec.get('passed') is True and not rec.get('errors'), prefix + 'case failed', errors)
        check(rec['process'].get('returncode') == 0 and rec['process'].get('exception') is None,
              prefix + 'process failed', errors)
        csv_path = case_dir / 'native.csv'
        check(sha(csv_path) == rec['timing_csv_sha256'], prefix + 'CSV hash mismatch', errors)
        rows = read_csv(csv_path)
        expected = {(method, rep) for method in job['methods'] for rep in range(job['repetitions'])}
        observed = Counter((r['method'], int(r['repetition'])) for r in rows)
        check(observed == Counter(expected), prefix + 'method/repetition coverage mismatch', errors)
        check(job['repetitions'] == 7, prefix + 'not seven repetitions', errors)
        check(len(rows) == meta['timed_records'] == rec['timing_rows'] == len(expected),
              prefix + 'record count mismatch', errors)
        for flag in ('all_exact_distances_passed', 'all_queues_drained', 'common_lifo_work_counts_passed'):
            check(meta.get(flag) is True, prefix + flag, errors)
        for field, expected_value in [('source', job['query_source']), ('train', job['train_source']),
                                      ('repeat', 7), ('seed', job['paired_seed']), ('methods', job['methods'])]:
            check(meta.get(field) == expected_value, prefix + field + ' mismatch', errors)
        row_map, orders, seeds = {}, defaultdict(list), defaultdict(set)
        for row in rows:
            method, rep = row['method'], int(row['repetition'])
            row_map[method, rep] = row
            orders[rep].append((int(row['order']), method))
            seeds[rep].add(int(row['height_seed']))
            check(row['correct'] == row['drained'] == 'true', prefix + 'row correctness/drain failed', errors)
            check(row['distance_fnv1a64'] == str(meta['distance_fnv1a64']), prefix + 'distance digest mismatch', errors)
            check(int(row['source']) == job['query_source'] and int(row['train']) == job['train_source'],
                  prefix + 'row source mismatch', errors)
            for clock in CLOCKS:
                value = float(row[clock + '_seconds'])
                check(math.isfinite(value) and value > 0, prefix + 'invalid ' + clock + ' duration', errors)
            timing_ratios.append(float(row['wall_seconds']) / float(row['cpu_seconds']))
        for rep in range(7):
            expected_seed = (job['paired_seed'] + (rep + 1) * 0x9e3779b97f4a7c15) & ((1 << 64) - 1)
            check(seeds[rep] == {expected_seed}, prefix + 'height-seed mismatch', errors)
            check(sorted(order for order, _ in orders[rep]) == list(range(len(job['methods']))),
                  prefix + 'order is not a permutation', errors)
        diag = meta['diagnostics_outside_timing']
        check(set(diag) == set(job['methods']), prefix + 'warmup method coverage mismatch', errors)
        work = {(v['insertions'], v['extractions'], v['peak_entries']) for v in diag.values()}
        check(len(work) == 1 and all(v['insertions'] == v['extractions'] for v in diag.values()),
              prefix + 'work count or drain mismatch', errors)
        verification = rec['distance_verification']
        distance_path = case_dir / 'distances.f64le'
        check(verification['passed'] is True, prefix + 'distance verification failed', errors)
        check(sha(distance_path) == verification['actual_sha256'] == verification['expected_sha256']
              == job['oracle_binary64_sha256'], prefix + 'independent distance bytes mismatch', errors)
        check(distance_path.stat().st_size == verification['actual_bytes'] == verification['expected_bytes']
              == job['oracle_binary64_bytes'], prefix + 'distance size mismatch', errors)
        raw[case_id] = row_map
        case_audits.append({'case_id': case_id, 'workload': job['workload'], 'method_count': len(job['methods']),
                            'repetitions': 7, 'records': len(rows), 'paired_seed': job['paired_seed'],
                            'height_seeds': [next(iter(seeds[r])) for r in range(7)],
                            'actual_orders': [[m for _, m in sorted(orders[r])] for r in range(7)]})
        provenance[case_id] = {name: sha(case_dir / name) for name in ('native.csv', 'native.json', 'case_record.json', 'distances.f64le')}
    return {'root': str(root), 'document': document, 'jobs': jobs, 'rows': raw,
            'audit': {'errors': errors, 'passed': not errors, 'cases': len(jobs),
                      'timings': sum(len(v) for v in raw.values()), 'case_details': case_audits,
                      'scheduling': {'wall_cpu_gt_1_10': sum(x > 1.1 for x in timing_ratios),
                                     'wall_cpu_gt_1_25': sum(x > 1.25 for x in timing_ratios),
                                     'max_wall_cpu_ratio': max(timing_ratios), 'no_records_filtered': True},
                      'input_sha256': {'native_experiment.json': sha(root / 'native_experiment.json'),
                                       'native_protocol.json': sha(root / 'native_protocol.json')},
                      'raw_file_sha256': provenance}}


def quantile(values, p):
    values = sorted(values)
    position = p * (len(values) - 1)
    lo, hi = math.floor(position), math.ceil(position)
    return values[lo] + (position - lo) * (values[hi] - values[lo])


def cluster_interval(units, clock, draws):
    clusters = defaultdict(lambda: defaultdict(list))
    for unit in units:
        clusters[unit['train_source']][unit['query_group']].append(unit[clock + '_ratio'])
    rng = random.Random(587113)
    names = sorted(clusters)
    estimates = []
    for _ in range(draws):
        sample = []
        for train in rng.choices(names, k=len(names)):
            for group in sorted(clusters[train]):
                values = clusters[train][group]
                sample.extend(rng.choices(values, k=len(values)))
        estimates.append(st.median(sample))
    return [quantile(estimates, .025), quantile(estimates, .975)]


def summarize_host(host, draws):
    sources, repeats, groups = [], [], defaultdict(list)
    for case_id, job in host['jobs'].items():
        pairs = {(method, base) for method in job['methods'] for base in ('direct_heap', 'std_heap')}
        pairs.update((a, b) for a, b in ABLATIONS if a in job['methods'] and b in job['methods'])
        for numerator, denominator in sorted(pairs):
            unit = {k: job[k] for k in ('case_id', 'workload', 'train_source', 'query_source', 'train_index')}
            unit.update(query_group=job['source_group'], numerator=numerator, denominator=denominator, repetitions=7)
            per_clock = defaultdict(list)
            for rep in range(7):
                a, b = host['rows'][case_id][numerator, rep], host['rows'][case_id][denominator, rep]
                paired = {**unit, 'repetition': rep, 'height_seed': int(a['height_seed']),
                          'numerator_order': int(a['order']), 'denominator_order': int(b['order'])}
                for clock in CLOCKS:
                    value = float(a[clock + '_seconds']) / float(b[clock + '_seconds'])
                    paired[clock + '_ratio'] = value
                    per_clock[clock].append(value)
                repeats.append(paired)
            for clock in CLOCKS:
                unit[clock + '_ratio'] = st.median(per_clock[clock])
            sources.append(unit)
            groups[job['workload'], numerator, denominator].append(unit)
    summaries, rankings = [], []
    for (workload, numerator, denominator), units in sorted(groups.items()):
        if len(units) != 6 or len({u['train_source'] for u in units}) != 3:
            raise ValueError('expected six units and three clusters: ' + workload)
        for train in {u['train_source'] for u in units}:
            if Counter(u['query_group'] for u in units if u['train_source'] == train) != Counter({'near': 1, 'far': 1}):
                raise ValueError('expected one near and one far per training cluster: ' + workload)
        item = {'workload': workload, 'numerator': numerator, 'denominator': denominator,
                'source_units': 6, 'training_clusters': 3, 'nested_repetitions': 42}
        for clock in CLOCKS:
            values = [u[clock + '_ratio'] for u in units]
            item[clock + '_median'] = st.median(values)
            item[clock + '_source_range'] = [min(values), max(values)]
            item[clock + '_exploratory_interval'] = cluster_interval(units, clock, draws)
            item[clock + '_sources_below_one'] = sum(v < 1 for v in values)
            for group in ('near', 'far'):
                item[clock + '_' + group + '_median'] = st.median(u[clock + '_ratio'] for u in units if u['query_group'] == group)
        summaries.append(item)
    for workload in host['document']['protocol']['selected_workloads']:
        for clock in CLOCKS:
            values = sorted((r[clock + '_median'], r['numerator']) for r in summaries
                            if r['workload'] == workload and r['denominator'] == 'direct_heap')
            for rank, (value, method) in enumerate(values, 1):
                rankings.append({'workload': workload, 'clock': clock, 'method': method,
                                 'rank': rank, 'ratio_to_direct_heap': value})
    return {'source_units': sources, 'paired_repetitions': repeats, 'workload_contrasts': summaries, 'rankings': rankings}


def compare_hosts(hosts, summaries):
    a, b = hosts['mac'], hosts['windows_wsl2']
    errors, differences = [], []
    check(set(a['jobs']) == set(b['jobs']), 'cross-host cases differ', errors)
    check([j['case_id'] for j in a['document']['protocol']['jobs']] == [j['case_id'] for j in b['document']['protocol']['jobs']],
          'cross-host planned case order differs', errors)
    check([r['case']['case_id'] for r in a['document']['cases']] == [r['case']['case_id'] for r in b['document']['cases']],
          'cross-host actual case order differs', errors)
    for field in ('source_sha256', 'compile_flags'):
        check(a['document']['build'][field] == b['document']['build'][field], 'cross-host build ' + field + ' differs', errors)
    for field in ('protocol_seed', 'repetitions', 'selected_workloads', 'runner_sha256',
                  'workload_generator_sha256', 'workload_manifest_sha256', 'no_fast_math'):
        check(a['document']['protocol'][field] == b['document']['protocol'][field], 'cross-host protocol ' + field + ' differs', errors)
    input_maps = [{name: {k: v for k, v in item.items() if k != 'path'}
                   for name, item in host['document']['protocol']['exported_inputs'].items()} for host in (a, b)]
    check(input_maps[0] == input_maps[1], 'cross-host exported input hashes/properties differ', errors)
    metadata = [{r['case']['case_id']: r['native_metadata'] for r in host['document']['cases']} for host in (a, b)]
    for case_id, ja in a['jobs'].items():
        jb = b['jobs'][case_id]
        for field in set(ja) | set(jb):
            if field != 'oracle_binary64_path':
                check(ja.get(field) == jb.get(field), case_id + ': cross-host job ' + field + ' differs', errors)
        for field in ('vertices', 'arcs', 'train_vertices', 'train_arcs', 'training_keys',
                      'rank_universe', 'radix_eligible', 'diagnostics_outside_timing'):
            check(metadata[0][case_id][field] == metadata[1][case_id][field],
                  case_id + ': cross-host metadata ' + field + ' differs', errors)
        for key, ra in a['rows'][case_id].items():
            rb = b['rows'][case_id][key]
            check(ra['height_seed'] == rb['height_seed'], case_id + ': cross-host seed mismatch', errors)
            check(ra['distance_fnv1a64'] == rb['distance_fnv1a64'], case_id + ': cross-host distance mismatch', errors)
            if ra['order'] != rb['order']:
                differences.append({'case_id': case_id, 'method': key[0], 'repetition': key[1],
                                    'mac_order': int(ra['order']), 'windows_wsl2_order': int(rb['order'])})
    maps = {h: {(r['workload'], r['numerator'], r['denominator']): r for r in s['workload_contrasts']}
            for h, s in summaries.items()}
    contrast_rows = []
    for key in sorted(maps['mac']):
        row = dict(zip(('workload', 'numerator', 'denominator'), key))
        for clock in CLOCKS:
            for host in hosts:
                entry = maps[host][key]
                row[host + '_' + clock + '_ratio'] = entry[clock + '_median']
            row[clock + '_direction_changed'] = ((row['mac_' + clock + '_ratio'] - 1) *
                                                 (row['windows_wsl2_' + clock + '_ratio'] - 1) < 0)
        contrast_rows.append(row)
    return {'passed': not errors, 'errors': errors, 'method_order_cells_differing': len(differences),
            'method_order_cells_total': a['audit']['timings'],
            'repetition_orders_differing': len({(r['case_id'], r['repetition']) for r in differences}),
            'order_differences': differences,
            'interpretation': 'Numeric height seeds match. Actual method permutations differ; std::shuffle need not produce identical permutations across C++ standard-library implementations. No cross-host paired timing claim is made.'}, contrast_rows


def audit_supplied(comparison_dir, summaries):
    errors, compared = [], 0
    lookup = {h: {(r['workload'], r['numerator'], r['denominator']): r for r in s['workload_contrasts']}
              for h, s in summaries.items()}
    supplied = read_csv(comparison_dir / 'native_paired_ratios_side_by_side.csv')
    expected_keys = {(r['workload'], r['method']) for r in summaries['mac']['rankings'] if r['clock'] == 'wall'}
    check(Counter((r['workload'], r['method']) for r in supplied) == Counter(expected_keys), 'supplied ratio coverage differs', errors)
    for row in supplied:
        for host, label in [('mac', 'Mac'), ('windows_wsl2', 'Windows_WSL2')]:
            for clock in CLOCKS:
                for baseline in ('direct_heap', 'std_heap'):
                    actual = lookup[host][row['workload'], row['method'], baseline][clock + '_median']
                    reported = float(row[label + '_' + clock + '_ratio_to_' + baseline])
                    check(math.isclose(actual, reported, rel_tol=1e-12, abs_tol=1e-12), 'supplied ratio differs: ' + str(row), errors)
                    compared += 1
    rankings = {h: {(r['workload'], r['method']): r['rank'] for r in s['rankings'] if r['clock'] == 'wall'}
                for h, s in summaries.items()}
    rank_rows = read_csv(comparison_dir / 'native_rankings_side_by_side.csv')
    check(Counter((r['workload'], r['method']) for r in rank_rows) == Counter(expected_keys), 'supplied ranking coverage differs', errors)
    for row in rank_rows:
        for host, label in [('mac', 'Mac'), ('windows_wsl2', 'Windows_WSL2')]:
            check(int(row[label + '_rank']) == rankings[host][row['workload'], row['method']], 'supplied rank differs', errors)
    fastest_rows = read_csv(comparison_dir / 'native_fastest_methods.csv')
    expected_workloads = {w for w, _ in expected_keys}
    check(Counter(r['workload'] for r in fastest_rows) == Counter(expected_workloads), 'supplied fastest coverage differs', errors)
    for row in fastest_rows:
        for host, label in [('mac', 'Mac'), ('windows_wsl2', 'Windows_WSL2')]:
            fastest = [method for (workload, method), rank in rankings[host].items()
                       if workload == row['workload'] and rank == 1]
            check(fastest == [row[label + '_fastest']], 'supplied fastest method differs', errors)
    return {'passed': not errors, 'errors': errors, 'numeric_ratio_cells_compared': compared,
            'ranking_cells_compared': len(rank_rows) * 2,
            'fastest_method_cells_compared': len(fastest_rows) * 2,
            'note': 'Supplied comparisons are checked only after independent raw-CSV aggregation.'}


def write_csv(path, rows):
    if not rows:
        return
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mac', type=Path, default=HERE.parent / 'phase2' / 'native_results')
    parser.add_argument('--windows', type=Path, default=HERE / 'imported' / 'second_host_native')
    parser.add_argument('--comparison', type=Path, default=HERE / 'imported' / 'comparison')
    parser.add_argument('--output', type=Path, default=HERE / 'native_cross_host_summary.json')
    parser.add_argument('--tables', type=Path, default=HERE / 'tables')
    parser.add_argument('--bootstrap-draws', type=int, default=2000)
    args = parser.parse_args()
    if args.bootstrap_draws < 1:
        raise ValueError('bootstrap draws must be positive')
    hosts = {'mac': load_host(args.mac), 'windows_wsl2': load_host(args.windows)}
    errors = {h: d['audit']['errors'] for h, d in hosts.items() if d['audit']['errors']}
    if errors:
        raise ValueError(json.dumps(errors, indent=2))
    summaries = {h: summarize_host(d, args.bootstrap_draws) for h, d in hosts.items()}
    cross, contrasts = compare_hosts(hosts, summaries)
    supplied = audit_supplied(args.comparison, summaries)
    rank_rows = []
    rank_maps = {h: {(r['workload'], r['clock'], r['method']): r for r in s['rankings']} for h, s in summaries.items()}
    for key in sorted(rank_maps['mac']):
        ma, wi = rank_maps['mac'][key], rank_maps['windows_wsl2'][key]
        rank_rows.append({'workload': key[0], 'clock': key[1], 'method': key[2],
                          'mac_rank': ma['rank'], 'windows_wsl2_rank': wi['rank'],
                          'rank_changed': ma['rank'] != wi['rank'],
                          'mac_ratio_to_direct_heap': ma['ratio_to_direct_heap'],
                          'windows_wsl2_ratio_to_direct_heap': wi['ratio_to_direct_heap']})
    result = {'schema_version': 1, 'analysis_source_sha256': sha(__file__),
              'audits': {h: d['audit'] for h, d in hosts.items()}, 'cross_host_audit': cross,
              'supplied_comparison_audit': supplied,
              'host_environments': {h: {'runtime': d['document']['runtime'], 'build': d['document']['build']} for h, d in hosts.items()},
              'hosts': {h: {k: v for k, v in s.items() if k != 'paired_repetitions'} for h, s in summaries.items()},
              'side_by_side_contrasts': contrasts, 'side_by_side_rankings': rank_rows,
              'direction_changes': [r for r in contrasts if any(r[c + '_direction_changed'] for c in CLOCKS)],
              'headline': {
                  'rank_positions_changed_of_82': {c: sum(r['rank_changed'] for r in rank_rows if r['clock'] == c) for c in CLOCKS},
                  'fastest_methods_by_host_and_clock': {
                      h: {c: {r['workload']: r['method'] for r in s['rankings'] if r['clock'] == c and r['rank'] == 1}
                          for c in CLOCKS} for h, s in summaries.items()},
                  'guided_plain_point_medians_below_one_of_42': {
                      h: {c: sum(r[c + '_median'] < 1 for r in s['workload_contrasts']
                                 if r['numerator'].startswith('guided_') and r['denominator'] == 'plain_skiplist')
                          for c in CLOCKS} for h, s in summaries.items()},
                  'bitmap_set_point_medians_below_one_of_21': {
                      h: {c: sum(r[c + '_median'] < 1 for r in s['workload_contrasts']
                                 if r['numerator'].startswith('guided_bitmap_') and r['denominator'].startswith('guided_set_'))
                          for c in CLOCKS} for h, s in summaries.items()}},
              'interpretation': [
                  'Every ratio is within one host/case/repetition. Seven repetitions collapse before the six source units; hardware samples are never pooled.',
                  'Ranks sort median source-level paired ratios to direct_heap, separately for CPU and wall clocks; they are descriptive and do not incorporate an equivalence threshold.',
                  'Intervals use 2000 deterministic percentile resamples of three training-source clusters, preserving one near and one far query per selected cluster. They are exploratory and conditional on fixed graph instances.',
                  'The second native run uses Windows-hosted WSL2 Linux, GCC, x86_64; the first uses macOS, Apple Clang, ARM. These jointly changed factors and their runtime libraries cannot be isolated as architecture effects.',
                  'Direct heap removes the common routing map while preserving tie order. Shared std_heap is the queue-interface control. Bitmap/set contrasts isolate native predecessor index; guided/plain includes advice and rank-bucket work.',
                  'The native grouped queues are separately engineered controls; the Python artifact and phase-three occurrence ablations were not rerun by these native records.',
                  'No timing-based exclusions or imported script/binary execution. Source hashes and raw record integrity are audited; reported correctness flags are supported by independently rehashed distance files.'
              ]}
    result['interpretation'][2] = result['interpretation'][2].replace('2000', str(args.bootstrap_draws))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.tables.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    write_csv(args.tables / 'native_cross_host_contrasts.csv', contrasts)
    write_csv(args.tables / 'native_cross_host_rankings.csv', rank_rows)
    write_csv(args.tables / 'native_cross_host_source_units.csv', [{**u, 'host': h} for h, s in summaries.items() for u in s['source_units']])
    write_csv(args.tables / 'native_cross_host_paired_repetitions.csv', [{**u, 'host': h} for h, s in summaries.items() for u in s['paired_repetitions']])
    estimate_rows = []
    for host, summary in summaries.items():
        for estimate in summary['workload_contrasts']:
            flat = {'host': host, **estimate}
            for clock in CLOCKS:
                for name in ('source_range', 'exploratory_interval'):
                    values = flat.pop(clock + '_' + name)
                    flat[clock + '_' + name + '_lower'] = values[0]
                    flat[clock + '_' + name + '_upper'] = values[1]
            estimate_rows.append(flat)
    write_csv(args.tables / 'native_cross_host_estimates.csv', estimate_rows)
    print(json.dumps({'output': str(args.output), 'host_audits_passed': all(d['audit']['passed'] for d in hosts.values()),
                      'cross_host_audit_passed': cross['passed'], 'supplied_comparisons_match': supplied['passed'],
                      'timings_per_host': {h: d['audit']['timings'] for h, d in hosts.items()},
                      'direction_change_rows': len(result['direction_changes'])}, indent=2))
    if not cross['passed'] or not supplied['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
