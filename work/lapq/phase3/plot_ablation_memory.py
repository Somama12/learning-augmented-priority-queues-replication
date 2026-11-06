#!/usr/bin/env python3
"""Plot completed bounded-live memory evidence; no experiment runs here."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

HERE=Path(__file__).resolve().parent
OUT=HERE.parents[2]/'outputs'
summary=json.loads((HERE/'ablation_summary.json').read_text())
if not summary['attribution_gate_passed']:
    raise RuntimeError('Complete passing evidence required')
methods=['occ_list_retain','occ_list_map','occ_linked_retain','occ_linked_map','grouped_compact_reference']
labels=['List\nhistory','List\ncleanup','Linked\nhistory','Linked\ncleanup','Compact\n(coupled)']
fig,axes=plt.subplots(1,2,figsize=(9.4,3.7),sharey=True)
for ax,mode,title in zip(axes,['enabled','disabled_during_trace'],['Automatic GC enabled','Automatic GC disabled']):
    rows=[next(r for r in summary['memory_runs'] if r['kind']=='retention' and r['spec']['method']==m
               and r['spec']['gc_mode']==mode and r['spec']['insertions']==100000) for m in methods]
    x=np.arange(len(methods))
    for offset,field,color,label in [(-.18,'python_traced_empty_queue_alive_before_gc_bytes','#2b6cb0','Before full GC'),
                                    (.18,'python_traced_empty_queue_alive_after_gc_bytes','#d97706','After full GC')]:
        ax.bar(x+offset,[r[field]/1024 for r in rows],width=.34,color=color,label=label,zorder=3)
    ax.set_xticks(x,labels,fontsize=9)
    ax.set_yscale('log')
    ax.set_ylim(1,100000)
    ax.set_title(title,fontsize=11,pad=10)
    ax.grid(axis='y',which='major',color='#d9dee4',linewidth=.6,zorder=0)
    ax.tick_params(axis='x',length=0,pad=6)
    for side in ['top','right']:ax.spines[side].set_visible(False)
axes[0].set_ylabel('Traced memory with empty queue alive (KiB)',fontsize=10)
handles,legend_labels=axes[0].get_legend_handles_labels()
fig.legend(handles,legend_labels,loc='upper center',ncol=2,frameon=False,fontsize=10,bbox_to_anchor=(.53,1.01))
fig.subplots_adjust(left=.09,right=.985,bottom=.19,top=.82,wspace=.08)
path=OUT/'learned-pq-ablation-memory.png'
fig.savefig(path,dpi=240,facecolor='white')
plt.close(fig)
print(path)
