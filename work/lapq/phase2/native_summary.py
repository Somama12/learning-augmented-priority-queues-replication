"""Paired native summaries; repetitions are nested, not independent datasets.

No execution on import. Source-level estimates collapse paired repetition ratios
before graph-level summaries. Exploratory intervals resample training-source
clusters, preserving the frozen near/far composition inside each cluster.
Every raw record is retained by the runner; failed/incomplete cases are listed
and excluded as whole cases, never filtered by timing value or winning method.
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
import statistics

HERE = Path(__file__).resolve().parent
BASELINES = ('direct_heap', 'std_heap')
ABLATIONS = [(f'guided_bitmap_{a}', f'guided_set_{a}') for a in ('exact', 'linear', 'hist64')]
ABLATIONS += [(f'guided_{index}_{a}', 'plain_skiplist')
              for index in ('set', 'bitmap') for a in ('exact', 'linear', 'hist64')]
ABLATIONS += [('grouped_heap', 'direct_heap'), ('grouped_heap', 'std_heap'),
              ('std_heap', 'direct_heap')]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def quantile(values, p):
    ordered = sorted(values)
    position = (len(ordered)-1)*p
    lower = int(position)
    upper = min(lower+1, len(ordered)-1)
    return ordered[lower] + (position-lower)*(ordered[upper]-ordered[lower])


def cluster_interval(units, field, draws=2000, seed=587113):
    clusters = defaultdict(lambda: defaultdict(list))
    for unit in units:
        clusters[unit['train_source']][unit['query_group']].append(unit[field])
    if len(clusters) < 2:
        return None
    rng = random.Random(seed)
    names = sorted(clusters)
    estimates = []
    for _ in range(draws):
        sample = []
        for name in rng.choices(names, k=len(names)):
            for group in sorted(clusters[name]):
                values = clusters[name][group]
                sample.extend(rng.choices(values, k=len(values)))
        estimates.append(statistics.median(sample))
    return {'lower': quantile(estimates, .025), 'upper': quantile(estimates, .975),
            'resamples': draws, 'training_source_clusters': len(clusters),
            'method': 'Exploratory hierarchical percentile bootstrap: resample training sources, then query sources within each frozen near/far stratum; repetition pairs collapsed first.',
            'limitation': 'Only three training sources per graph and one near/one far query per source. Not uncertainty across independent graphs; do not infer broad significance.'}


def advisor_of(method):
    if method.startswith('guided_'):
        return method.rsplit('_', 1)[1]
    return None


def aggregate(units, draws):
    if not units:
        return None
    result = {'heldout_source_units': len(units),
              'training_sources': len({u['train_source'] for u in units}),
              'nested_timing_repeats': sum(u['repeat_count'] for u in units)}
    for clock in ('wall', 'cpu'):
        values = [u[f'{clock}_ratio_median'] for u in units]
        result[f'source_median_{clock}_ratio'] = statistics.median(values)
        result[f'observed_source_{clock}_ratio_range'] = [min(values), max(values)]
        result[f'{clock}_ratio_cluster_interval'] = cluster_interval(units, f'{clock}_ratio_median', draws)
        if f'cold_break_even_queries_{clock}' in units[0]:
            result[f'sources_without_positive_{clock}_saving'] = sum(u[f'cold_break_even_queries_{clock}'] is None for u in units)
    return result


def make_source_unit(job, numerator, denominator, paired, metadata, cold=False):
    unit = {'case_id': job['case_id'], 'workload': job['workload'],
            'training_graph': job['training_graph'], 'train_source': job['train_source'],
            'query_source': job['query_source'], 'query_group': job['source_group'],
            'method': numerator, 'baseline': denominator,
            'repeat_count': len(paired), 'paired_repeats': paired}
    for clock in ('wall', 'cpu'):
        unit[f'{clock}_ratio_median'] = statistics.median(r[f'{clock}_ratio'] for r in paired)
        unit[f'paired_saving_{clock}_seconds_median'] = statistics.median(r[f'paired_saving_{clock}_seconds'] for r in paired)
        unit[f'query_{clock}_seconds_median'] = statistics.median(r[f'numerator_{clock}_seconds'] for r in paired)
        unit[f'baseline_{clock}_seconds_median'] = statistics.median(r[f'denominator_{clock}_seconds'] for r in paired)
        if cold:
            advisor = advisor_of(numerator)
            cost = 0.0
            if advisor:
                cost = float(metadata[f'training_{clock}_seconds']) + float(metadata['build_times'][advisor][f'{clock}_seconds'])
            unit[f'cold_training_plus_build_{clock}_seconds'] = cost
            saving = unit[f'paired_saving_{clock}_seconds_median']
            unit[f'cold_break_even_queries_{clock}'] = cost/saving if saving > 0 else None
            unit[f'cold_minimum_integer_queries_{clock}'] = math.floor(cost/saving)+1 if saving > 0 else None
    if cold:
        unit['training_cost_measurements'] = 1
        unit['cold_interpretation'] = 'Hypothetical repeated query from this heldout source using one frozen fitted model: q > T / median paired (baseline - method) query saving. Training/build measured once in this case, not independently seven times.'
    return unit


def paired_records(rows_by_method, numerator, denominator, repeats):
    output = []
    for repetition in range(repeats):
        a = rows_by_method[(numerator, repetition)]
        b = rows_by_method[(denominator, repetition)]
        record = {'repetition': repetition, 'height_seed': int(a['height_seed']),
                  'numerator_order': int(a['order']), 'denominator_order': int(b['order'])}
        for clock in ('wall', 'cpu'):
            aa, bb = float(a[f'{clock}_seconds']), float(b[f'{clock}_seconds'])
            if bb <= 0:
                raise ValueError(f'nonpositive denominator {clock} time')
            record[f'{clock}_ratio'] = aa / bb
            record[f'paired_saving_{clock}_seconds'] = bb - aa
            record[f'numerator_{clock}_seconds'] = aa
            record[f'denominator_{clock}_seconds'] = bb
        output.append(record)
    return output


def summarize(input_path, draws=2000):
    from native_experiment import validate_native_records
    input_path = Path(input_path)
    document = json.loads(input_path.read_text())
    plan = document['protocol']
    expected = {job['case_id']: job for job in plan['jobs']}
    if len(expected) != len(plan['jobs']):
        raise ValueError('duplicate native planned case ID')
    records = document['cases']
    ids = [r['case']['case_id'] for r in records]
    duplicates = [key for key, count in Counter(ids).items() if count > 1]
    if duplicates:
        raise ValueError('duplicate native observed case ID: ' + repr(duplicates))
    source_units, ablation_units, failures, training_records = [], [], [], []
    all_rows = []
    for record in records:
        job = record['case']
        case_id = job['case_id']
        errors = list(record.get('errors', []))
        if case_id not in expected or job != expected[case_id]:
            errors.append('observed case differs from frozen protocol')
        if record.get('passed') is not True:
            errors.append('native driver marked case failed')
        metadata = record.get('native_metadata', {})
        rows = []
        # Raw JSON retains original absolute provenance. Prefer the case sibling
        # when analyzing a relocated archive, and keep the same hash check below.
        local_csv = input_path.parent / 'cases' / case_id / 'native.csv'
        path = (local_csv if local_csv.exists() else
                Path(record['timing_csv']) if record.get('timing_csv') else None)
        if path is None or not path.exists():
            errors.append('timing CSV missing')
        else:
            if sha256(path) != record.get('timing_csv_sha256'):
                errors.append('timing CSV changed after run')
            with path.open(newline='') as stream:
                rows = list(csv.DictReader(stream))
            all_rows.extend({'case_id': case_id, **row} for row in rows)
            errors.extend(validate_native_records(metadata, rows, job))
        if record.get('distance_verification', {}).get('passed') is not True:
            errors.append('independent Python/native distance verification did not pass')
        if errors:
            failures.append({'case_id': case_id, 'errors': list(dict.fromkeys(errors)),
                             'raw_timing_rows_preserved': len(rows)})
            continue
        row_map = {(row['method'], int(row['repetition'])): row for row in rows}
        case_sources, case_ablations = [], []
        try:
            for method in job['methods']:
                for baseline in BASELINES:
                    paired = paired_records(row_map, method, baseline, job['repetitions'])
                    case_sources.append(make_source_unit(job, method, baseline, paired, metadata, cold=True))
            for numerator, denominator in ABLATIONS:
                if numerator not in job['methods'] or denominator not in job['methods']:
                    continue
                paired = paired_records(row_map, numerator, denominator, job['repetitions'])
                case_ablations.append(make_source_unit(job, numerator, denominator, paired, metadata))
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
            failures.append({'case_id': case_id, 'errors': ['paired analysis failed: ' + repr(error)],
                             'raw_timing_rows_preserved': len(rows)})
            continue
        source_units.extend(case_sources)
        ablation_units.extend(case_ablations)
        training_records.append({'case_id': case_id, 'workload': job['workload'],
            'training_graph': job['training_graph'], 'train_source': job['train_source'],
            'query_source': job['query_source'], 'training_keys': metadata['training_keys'],
            'training_wall_seconds': metadata['training_wall_seconds'],
            'training_cpu_seconds': metadata['training_cpu_seconds'], 'build_times': metadata['build_times']})
    workloads = {}
    for workload in plan['selected_workloads']:
        selected = [u for u in source_units if u['workload'] == workload]
        methods = {}
        for method in sorted({u['method'] for u in selected}):
            methods[method] = {}
            for baseline in BASELINES:
                units = [u for u in selected if u['method'] == method and u['baseline'] == baseline]
                stats = aggregate(units, draws)
                if stats is not None:
                    stats['by_query_group'] = {group: aggregate([u for u in units if u['query_group'] == group], draws)
                                               for group in ('near', 'far')}
                methods[method][baseline] = stats
        comparisons = {}
        for numerator, denominator in ABLATIONS:
            units = [u for u in ablation_units if u['workload'] == workload and
                     u['method'] == numerator and u['baseline'] == denominator]
            if units:
                stats = aggregate(units, draws)
                stats['by_query_group'] = {group: aggregate([u for u in units if u['query_group'] == group], draws)
                                           for group in ('near', 'far')}
                comparisons[numerator+'/'+denominator] = stats
        workloads[workload] = {'expected_source_units': 6, 'methods': methods,
                               'within_repetition_ablations': comparisons}
    missing = sorted(set(expected)-set(ids))
    unexpected = sorted(set(ids)-set(expected))
    complete = not missing and not unexpected and not failures
    return {'kind': 'native_phase2_summary', 'schema_version': 1,
        'input_file': str(input_path.resolve()), 'input_sha256': sha256(input_path),
        'summary_source_sha256': sha256(__file__), 'build': document['build'],
        'runtime': document['runtime'], 'protocol': plan,
        'coverage': {'planned_cases': len(expected), 'observed_cases': len(records),
                     'missing_cases': missing, 'unexpected_cases': unexpected,
                     'failed_cases': len(failures), 'complete_and_matching': complete},
        'failures': failures, 'workloads': workloads, 'source_units': source_units,
        'ablation_source_units': ablation_units, 'training_records': training_records,
        'raw_native_timing_records_seen': len(all_rows),
        'interpretation': [
            'Ratios below one mean faster than the named baseline; report both direct_heap deployment and std_heap shared-kernel comparisons.',
            'Ratios are formed within repetitions before taking each source median. Graph-level statistics summarize six heldout source units, not 42 independent timing samples.',
            'Intervals condition on one graph, three fitted training-source models and fixed near/far strata; they are exploratory, not confirmatory evidence across graphs.',
            'Each case measures training/build once. Cold thresholds use median paired savings, not differences of separately aggregated timing medians; null means no observed positive saving.',
            'Guided bitmap/set differences isolate predecessor indexing within the same engineered grouped skiplist; guided/plain comparisons also include rank-bucket and predictor work.',
            'The native implementation uses grouped live-only nodes and is an independent engineering control, not an exact reimplementation of every Python artifact detail.',
            'All predictors and training samples remain resident during per-case query timing; no native isolated per-method memory claim follows.',
            'Failed or incomplete cases are preserved and excluded as whole cases. No time-based outlier removal or winner-only source selection is performed.',
            'Classical radix/bitmap mechanisms are prior art; decrease-key implementations need update-semantic alignment with this lazy-insertion protocol.']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, default=HERE/'native_results'/'native_experiment.json')
    parser.add_argument('--output', type=Path, default=HERE/'native_results'/'native_summary.json')
    parser.add_argument('--bootstrap-draws', type=int, default=2000)
    args = parser.parse_args()
    if args.bootstrap_draws < 1:
        raise ValueError('bootstrap draws must be positive')
    summary = summarize(args.input, args.bootstrap_draws)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True)+'\n')
    print(json.dumps({'output': str(args.output), 'coverage': summary['coverage']}))


if __name__ == '__main__':
    main()
