#!/usr/bin/env python3
"""Deterministic, condition-shared sorting inputs; never run experiments on import.

Default decay sampling targets the same mathematical final-rank distribution as
the released random walk using a signed-category multinomial. It is not a
bit-for-bit replay of the authors' unseeded/interleaved NumPy random stream.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

NUMPY_VERSION = '2.2.4'
MASTER_SEED = 2026110607
MODELS = ('offline', 'online', 'dirty')
DECAY_MODES = ('multinomial', 'literal')
STREAM_DOMAIN = 'lapq-resciencec-sorting-inputs-v1'


def require_numpy_version():
    if np.__version__ != NUMPY_VERSION:
        raise RuntimeError(f'Frozen generator requires numpy=={NUMPY_VERSION}, found {np.__version__}')


def seed_words(condition_id, stream, master_seed=MASTER_SEED):
    message = f'{STREAM_DOMAIN}|{master_seed}|{condition_id}|{stream}'.encode()
    digest = hashlib.sha256(message).digest()
    return [int.from_bytes(digest[i:i+4], 'little') for i in range(0, 16, 4)]


def condition_spec(regime, n, parameter_index, repetition, master_seed=MASTER_SEED, decay_mode='multinomial'):
    if regime not in ('class', 'decay') or not isinstance(n, int) or n < 1:
        raise ValueError('Expected class/decay and positive integer n')
    if not 0 <= parameter_index <= 20 or not 0 <= repetition < 30:
        raise ValueError('Frozen grid is 21 parameter indices and 30 repetitions')
    if decay_mode not in DECAY_MODES:
        raise ValueError('Unknown decay sampler')
    maximum = n if regime == 'class' else int(n * np.sqrt(n))
    parameter = parameter_index * maximum // 20
    identifier = f'{regime}_n{n}_p{parameter_index:02d}_r{repetition:02d}'
    if regime == 'decay' and decay_mode == 'literal':
        identifier += '_literal'
    algorithm = ('class_interval_uniform_v1' if regime == 'class' else
                 'decay_signed_multinomial_v1' if decay_mode == 'multinomial' else 'decay_literal_walk_v1')
    return {'condition_id': identifier, 'regime': regime, 'n': n,
            'parameter_index': parameter_index, 'repetition': repetition,
            'parameter_name': 'c' if regime == 'class' else 'timesteps',
            'parameter': parameter, 'maximum_parameter': maximum,
            'effective_parameter': min(max(1, parameter), n+1) if regime == 'class' else parameter,
            'generation_algorithm': algorithm, 'master_seed': master_seed,
            'seeds': {s: seed_words(identifier, s, master_seed) for s in ('advice', 'order', 'heights')}}


def array_hash(values):
    """SHA256 of contiguous signed int64 little-endian values, without a header."""
    return hashlib.sha256(np.asarray(values, dtype='<i8').tobytes(order='C')).hexdigest()


def class_ranks(n, c, rng):
    c = min(max(1, int(c)), n+1)
    thresholds = np.sort(np.concatenate((np.array([0]), rng.choice(n, c-1, replace=False), np.array([n]))))
    ranks = np.arange(n, dtype=np.int64)
    for lo, hi in zip(thresholds[:-1], thresholds[1:]):
        lo, hi = int(lo), int(hi)
        if hi > lo:
            ranks[lo:hi] = rng.randint(lo, hi, size=hi-lo)
    return ranks, {'effective_classes': c,
                   'zero_width_threshold_intervals': int(np.count_nonzero(np.diff(thresholds) == 0)),
                   'thresholds_sha256': array_hash(thresholds)}


def decay_ranks(n, timesteps, rng, mode='multinomial', chunk_size=1_000_000):
    timesteps = int(timesteps)
    if timesteps < 0:
        raise ValueError('Negative timestep count')
    ranks = np.arange(n, dtype=np.int64)
    if mode == 'multinomial':
        probabilities = np.full(2*n, 1.0/(2*n), dtype=np.float64)
        counts = rng.multinomial(timesteps, probabilities).astype(np.int64, copy=False)
        if int(counts.sum()) != timesteps or np.any(counts < 0):
            raise RuntimeError('Invalid multinomial counts')
        ranks += counts[:n] - counts[n:]
        details = {'signed_categories': 2*n, 'category_order': 'all positive items, then all negative items',
                   'probability_rational': f'1/{2*n}', 'probability_float64': float(probabilities[0]),
                   'probability_vector_float64_sum': float(probabilities.sum()),
                   'signed_category_counts_sha256': array_hash(counts),
                   'sampler_note': 'NumPy RandomState.multinomial uses floating probabilities and a last-category residual; same ideal final-rank law, not identical finite-PRNG output to the released walk.'}
    elif mode == 'literal':
        # The released program draws every item first, then every Bernoulli sign.
        # Preserve this order; chunk only the latter vector and accumulation.
        selected = rng.choice(n, timesteps)
        for start in range(0, timesteps, chunk_size):
            end = min(start+chunk_size, timesteps)
            signs = np.where(rng.random_sample(end-start) < .5, 1.0, -1.0)
            ranks += np.bincount(selected[start:end], weights=signs, minlength=n).astype(np.int64)
        details = {'literal_walk_steps': timesteps, 'bernoulli_chunk_size': chunk_size,
                   'accumulation_note': 'Integer-valued histogram sums are exact in binary64 within the declared timestep range; no rank clipping.'}
    else:
        raise ValueError('Unknown decay mode')
    return ranks, details


def generate_condition(spec):
    require_numpy_version()
    n = spec['n']
    advice_rng = np.random.RandomState(spec['seeds']['advice'])
    if spec['regime'] == 'class':
        predictions, details = class_ranks(n, spec['parameter'], advice_rng)
    else:
        mode = 'literal' if spec['generation_algorithm'] == 'decay_literal_walk_v1' else 'multinomial'
        predictions, details = decay_ranks(n, spec['parameter'], advice_rng, mode)
    order = np.random.RandomState(spec['seeds']['order']).permutation(n).astype(np.int64, copy=False)
    heights = np.random.RandomState(spec['seeds']['heights']).geometric(.5, size=n).astype(np.int64, copy=False)
    metadata = {'spec': spec, 'numpy_version': np.__version__, 'rng': 'numpy.random.RandomState/MT19937',
                'seed_encoding': 'first16 SHA256 bytes as four little-endian uint32 words',
                'array_encoding': 'signed int64 little-endian, contiguous, no header',
                'hashes': {name: array_hash(values) for name, values in
                           [('predictions', predictions), ('order', order), ('heights', heights)]},
                'prediction_min': int(predictions.min()), 'prediction_max': int(predictions.max()),
                'maximum_absolute_rank_error': int(np.max(np.abs(predictions-np.arange(n)))),
                'negative_prediction_count': int(np.count_nonzero(predictions < 0)),
                'maximum_height': int(heights.max()), 'height_count': n,
                'prediction_details': details}
    return {'predictions': predictions, 'order': order, 'heights': heights, 'metadata': metadata}


def engine_record(condition, model):
    if model not in MODELS:
        raise ValueError('Unknown sorting model')
    return {'id': condition['metadata']['spec']['condition_id']+'__'+model, 'model': model,
            'predictions': condition['predictions'].tolist(),
            'order': condition['order'].tolist(), 'heights': condition['heights'].tolist()}


def reference_parity(reference_module):
    """Small validation against separately obtained, pinned predictions.py.

    Not a primary experiment. Multinomial is deliberately not compared bitwise
    to the literal walk: only the class and optional literal generators are.
    """
    require_numpy_version()
    checked = 0
    for n in (1, 2, 7, 32):
        for integer_seed in (0, 17, 101):
            for c in (0, 1, n//2, n, n+1):
                np.random.seed(integer_seed)
                expected = np.array([pair[0] for pair in reference_module.classPredictions(n, c)])
                actual, _ = class_ranks(n, c, np.random.RandomState(integer_seed))
                if not np.array_equal(expected, actual):
                    raise AssertionError(('class parity', n, c, integer_seed))
                checked += 1
            for t in (0, 1, 73):
                np.random.seed(integer_seed)
                expected = np.array([pair[0] for pair in reference_module.decayPredictions(n, t)])
                actual, _ = decay_ranks(n, t, np.random.RandomState(integer_seed), 'literal', chunk_size=11)
                if not np.array_equal(expected, actual):
                    raise AssertionError(('literal decay parity', n, t, integer_seed))
                checked += 1
    return {'status': 'passed', 'exact_class_and_literal_reference_cases': checked,
            'compressed_mode_bitwise_equivalence_claimed': False}


def small_selfcheck():
    """Check deterministic traces and exact ideal-law enumeration, not timings."""
    from collections import Counter
    from fractions import Fraction
    from itertools import product
    n, steps = 2, 4
    paths = Counter()
    for sequence in product(range(2*n), repeat=steps):
        delta = [0]*n
        for category in sequence:
            delta[category % n] += 1 if category < n else -1
        paths[tuple(delta)] += Fraction(1, (2*n)**steps)
    multinomial = Counter()
    for counts in product(range(steps+1), repeat=2*n):
        if sum(counts) != steps:
            continue
        coefficient = math.factorial(steps)
        for count in counts:
            coefficient //= math.factorial(count)
        delta = tuple(counts[j]-counts[n+j] for j in range(n))
        multinomial[delta] += Fraction(coefficient, (2*n)**steps)
    if paths != multinomial:
        raise AssertionError('Signed-multinomial mathematical law differs')
    cases = 0
    for regime in ('class', 'decay'):
        for index in (0, 1, 20):
            spec = condition_spec(regime, 32, index, 0)
            a, b = generate_condition(spec), generate_condition(spec)
            if a['metadata'] != b['metadata']:
                raise AssertionError('Non-reproducible metadata')
            if sorted(a['order']) != list(range(32)) or np.any(a['heights'] < 1):
                raise AssertionError('Invalid order/heights')
            if regime == 'decay':
                delta = a['predictions']-np.arange(32)
                t = spec['parameter']
                if int(np.abs(delta).sum()) > t or (int(delta.sum())-t) % 2:
                    raise AssertionError('Invalid signed-walk total or parity')
            for model in MODELS:
                if len(engine_record(a, model)['heights']) != 32:
                    raise AssertionError('Invalid engine record')
            cases += 1
    return {'status': 'passed', 'small_deterministic_conditions': cases,
            'exact_law_enumeration': {'n': n, 'timesteps': steps, 'literal_paths': (2*n)**steps},
            'primary_conditions_generated': 0}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selfcheck', action='store_true')
    args = parser.parse_args()
    print(json.dumps(small_selfcheck() if args.selfcheck else
                     {'status': 'generator_ready', 'primary_conditions_generated': 0}, indent=2))
