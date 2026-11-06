#!/usr/bin/env python3
"""Regenerate Figure 1 from existing evidence; styling only, no experiments.

Every plotted coordinate uses the original insertion count and measured bytes.
The MiB unit conversion is exact; no rounding, offsets, or interpolation is used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PHASE2 = HERE.parent / 'phase2'
os.environ.setdefault('MPLCONFIGDIR', str(HERE / 'mpl-cache'))

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter


def main():
    evidence = json.loads((PHASE2 / 'retention_results.json').read_text())
    styles = [
        ('heapq', 'heapq', dict(color='#58748a', linestyle=':', marker='s',
                              linewidth=1.8, markersize=5.5, zorder=2)),
        ('original_online', 'Original',
         dict(color='#c15c47', linestyle='-', marker='o', linewidth=2.7,
              markersize=10, markerfacecolor='none', markeredgewidth=1.8, zorder=3)),
        ('bitmap_online', 'Bitmap only',
         dict(color='#d78b19', linestyle='--', marker='^', linewidth=1.7,
              markersize=5.5, markerfacecolor='#d78b19', markeredgewidth=0.8,
              dashes=(5, 3), zorder=4)),
        ('guided_bitmap_compact', 'Compact',
         dict(color='#3c8b76', linestyle='-.', marker='D', linewidth=1.8,
              markersize=5.5, markerfacecolor='white', markeredgewidth=1.4, zorder=2)),
    ]
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10,
                         'axes.spines.top': False, 'axes.spines.right': False})
    fig, ax = plt.subplots(figsize=(8.6, 3.9))
    by_kind = {}
    for kind, label, style in styles:
        rows = sorted((r for r in evidence['records']
                       if r['kind'] == kind and r['status'] == 'ok'),
                      key=lambda r: r['insertions'])
        if [r['insertions'] for r in rows] != [1000, 10000, 100000]:
            raise ValueError(f'Unexpected evidence coverage for {kind}')
        by_kind[kind] = rows
        ax.plot([r['insertions'] for r in rows],
                [r['traced_current_empty_queue_alive'] / 2**20 for r in rows],
                label=label, **style)
    differences = [b['traced_current_empty_queue_alive'] - a['traced_current_empty_queue_alive']
                   for a, b in zip(by_kind['original_online'], by_kind['bitmap_online'])]
    if differences != [3733, 3733, 3733]:
        raise ValueError('Update the overlap annotation if the evidence changes')
    ax.set_xscale('log')
    ax.set_yscale('log')
    ax.set_xticks([1000, 10000, 100000], ['1,000', '10,000', '100,000'])
    ax.set_yticks([0.001, 0.01, 0.1, 1, 10, 100])
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f'{value:g}'))
    ax.set_ylim(0.0003, 100)
    ax.set_xlabel('Total insertions; maximum simultaneous entries = 128')
    ax.set_ylabel('Retained MiB after drain (log scale)')
    ax.legend(frameon=False, ncol=2, loc='lower right', bbox_to_anchor=(1, 0.11), handlelength=3.2,
              columnspacing=1.7, labelspacing=0.6)
    ax.text(0.025, 0.96,
            'Original and bitmap nearly overlap\n'
            'Bitmap is 3,733 bytes higher at each length',
            transform=ax.transAxes, ha='left', va='top', fontsize=9,
            color='#333333', linespacing=1.4,
            bbox=dict(facecolor='white', edgecolor='none', alpha=0.9, pad=3))
    ax.grid(which='major', alpha=0.20)
    ax.set_axisbelow(True)
    fig.tight_layout()
    destination = ROOT / 'outputs/learned-pq-bounded-live-retention.png'
    fig.savefig(destination, dpi=200)
    plt.close(fig)
    print(destination)


if __name__ == '__main__':
    main()
