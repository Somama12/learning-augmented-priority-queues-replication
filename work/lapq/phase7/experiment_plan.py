#!/usr/bin/env python3
"""Prepare only the premeasurement sorting-comparison design; never run models."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from input_generation import MASTER_SEED, MODELS, NUMPY_VERSION, condition_spec

HERE = Path(__file__).resolve().parent


def build_plan():
    sizes = (1000, 10000, 100000)
    conditions = [condition_spec(regime, n, index, repetition)
                  for regime in ('class', 'decay') for n in sizes
                  for index in range(21) for repetition in range(30)]
    seed_tuples = [tuple(c['seeds'][s]) for c in conditions for s in ('advice', 'order', 'heights')]
    if len(set(seed_tuples)) != len(seed_tuples):
        raise ValueError('Seed-domain collision')
    total_n = sum(c['n'] for c in conditions)
    return {'schema_version': 1, 'status': 'draft; root approval and full code/source freeze required before primary execution',
            'created_utc': datetime.now(timezone.utc).isoformat(), 'master_seed': MASTER_SEED,
            'objective': 'Comparison-count replication of class/decay sorting experiments, with an explicit common-input design and compiled source-equivalent engine subject to reference validation.',
            'sizes': list(sizes), 'parameter_levels': 21, 'repetitions_per_condition': 30,
            'models': list(MODELS), 'conditions': conditions,
            'condition_count': len(conditions), 'method_run_count': len(conditions)*len(MODELS),
            'reference_commit': '3915cc261e87949b2b0aa724673c2227492d1b08',
            'reference_files': {
                'experiments.py': {'git_blob_sha1': '40dc153782e691a1188377de0c902bff85ce00a4'},
                'predictions.py': {'git_blob_sha1': '68f60b4d96fbfe1d5d942d2706adfdfd2bd624ed'},
                'sorting.py': {'git_blob_sha1': '211374e66719d815f2d859dc9ad001d9a8c8a086'}},
            'reference_curves': {'n1000': 'Declared Appendix F/Figures6–7 anchor; released LAPQ means/std available, original repetition count in those saved aggregates is unknown. Do not turn author std into SE by assuming30.',
                                 'n10000_n100000': 'Main Figures2–3 target sizes; released data folder has BC23 baseline arrays but no corresponding LAPQ raw/aggregate sorting files. Published figure comparison must be separately identified.'},
            'advice_contract': {
                'class': 'c_i=floor(i*n/20). Released clamp min(max(1,c),n+1) retained. Threshold choice includes0, so duplicate zero/empty intervals are retained. Uniform integer ranks within each nonempty interval; no endpoint repair.',
                'decay': 't_i=floor(i*int(n*sqrt(n))/20). Ideal final advice sampled by Multinomial(t;1/(2n) repeated2n), positive categories first, then negative. rank[j]=j+Cplus[j]-Cminus[j]; negative and above-n ranks retained.',
                'mathematical_equivalence': 'Each literal step chooses one of2n signed-item categories uniformly and independently. Their category counts therefore have the multinomial law, and final ranks depend only on those counts. No path statistic is consumed by sorting.',
                'numerical_scope': 'NumPy multinomial takes float64 probabilities with last-category residual. This targets the same ideal model, not the same finite-PRNG output as the released walk. Keep optional literal sampler for reviewers; changing the primary sampler requires an explicit amended plan.',
                'literal_alternative': 'Full item draw before chunked signs; exact small same-seed parity checked against pinned predictions.py. Never silently mix literal and compressed conditions.'},
            'randomness_departures': [
                'The released experiment loops algorithms separately and uses an unseeded global NumPy stream for advice, shuffling and sampled heights.',
                'This plan uses three independent explicit MT19937 states per condition, seeded by domain-separated SHA256 words; all models share advice and the same height sequence by insertion step.',
                'Online and dirty models share one insertion permutation. Offline starts with ordinal keys and stably sorts by predicted rank, retaining ordinal order on ties.',
                'Geometric(.5) heights are explicit positive integers. Engine RNG is not used; byte hashes and seeds permit regeneration without storing every input.',
                'Mean/std comparisons reproduce a distributional experiment and controlled common inputs, not an unrecoverable bitwise replay of authors random states.'],
            'measurement_contract': 'Primary outputs are clean/dirty comparison counts and sorted-output checks; input generation, JSON transport, and engine wall time are not evidence of deployment speed.',
            'retention_policy': 'Generate one shared condition once, send its explicit arrays to all three models, save spec/seeds/algorithm/NumPy version/all array SHA256s and all outputs/failures, then release arrays. Do not retain all full inputs.',
            'cost_estimates_not_measurements': {
                'shared_prediction_positions': total_n, 'model_insertions': total_n*len(MODELS),
                'equivalent_literal_decay_steps': sum(c['parameter'] for c in conditions if c['regime']=='decay'),
                'primary_multinomial_categories_total': sum(2*c['n'] for c in conditions if c['regime']=='decay'),
                'all_shared_int64_arrays_if_saved_bytes': total_n*3*8,
                'largest_condition_three_int64_arrays_bytes': max(sizes)*3*8,
                'conservative_numpy_generator_scratch_estimate_bytes': max(sizes)*96,
                'literal_alternative_max_item_array_bytes': max(c['parameter'] for c in conditions)*8,
                'persistent_input_manifest_estimate_bytes': len(conditions)*2500,
                'scope': 'Arithmetic storage/work estimates, not measured memory or elapsed time. Python JSON objects, interpreter, compiled-engine nodes and results add overhead.',
                'duration': 'No measured duration forecast. After correctness validation, a separately labeled bounded throughput pilot can inform scheduling without becoming primary comparison evidence.'},
            'numpy_version': NUMPY_VERSION,
            'input_source_sha256': {name: hashlib.sha256((HERE/name).read_bytes()).hexdigest()
                                   for name in ('input_generation.py', 'experiment_plan.py')}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=HERE/'experiment_plan.json')
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Preserve an existing plan; select a fresh output path for an explicit revision')
    plan = build_plan()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, indent=2)+'\n')
    print(json.dumps({k:plan[k] for k in ('status','condition_count','method_run_count','cost_estimates_not_measurements')},indent=2))
