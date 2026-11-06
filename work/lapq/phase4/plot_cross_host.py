#!/usr/bin/env python3
"""Plot separately aggregated native estimates from two recorded hosts."""
from pathlib import Path
import json
import os
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
os.environ.setdefault('MPLCONFIGDIR',str(HERE/'mpl-cache'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

d=json.loads((HERE/'native_cross_host_summary.json').read_text())
if not all(a['passed'] for a in d['audits'].values()) or d['cross_host_audit']['errors']:
    raise RuntimeError('Passing raw-record audits required')
targets=['brussels','ny','grid_8100','geometric_8192','sparse_8192','sparse_2048__scale4','sparse_2048__lopsided']
names=['Brussels','New York','Grid 8,100','Geometric 8,192','Sparse 8,192','Sparse 2,048 x4','Sparse 2,048 lopsided']
fig,axes=plt.subplots(1,2,figsize=(9.6,4.1),sharey=True)
for ax,denominator,title in zip(axes,['direct_heap','guided_set_linear'],['Guided bitmap / direct heap','Guided bitmap / guided set']):
    for host,offset,color,label in [('mac',-.12,'#2463a7','Mac M1'),('windows_wsl2',.12,'#bd6509','Windows + WSL2')]:
        rows=[next(r for r in d['hosts'][host]['workload_contrasts'] if r['workload']==w
                   and r['numerator']=='guided_bitmap_linear' and r['denominator']==denominator) for w in targets]
        x=[r['cpu_median'] for r in rows]
        lower=[r['cpu_median']-r['cpu_exploratory_interval'][0] for r in rows]
        upper=[r['cpu_exploratory_interval'][1]-r['cpu_median'] for r in rows]
        ax.errorbar(x,[i+offset for i in range(len(rows))],xerr=[lower,upper],fmt='o',markersize=4,
                    color=color,linewidth=1.1,capsize=2,label=label)
    ax.axvline(1,color='#444444',linewidth=.9,linestyle='--')
    ax.set_xscale('log')
    ax.set_title(title,fontsize=11,pad=10)
    ax.set_xlabel('Paired CPU-time ratio (log scale)',fontsize=10)
    ax.grid(axis='x',which='both',color='#e0e4e8',linewidth=.5)
    ax.spines[['top','right']].set_visible(False)
axes[0].set_yticks(range(len(targets)),names,fontsize=9)
axes[0].invert_yaxis()
axes[0].set_xlim(.85,12.5)
axes[1].set_xlim(.75,1.11)
axes[1].set_xticks([.8,.9,1,1.1],['0.8','0.9','1.0','1.1'])
axes[1].minorticks_off()
h,l=axes[0].get_legend_handles_labels()
fig.legend(h,l,loc='upper center',ncol=2,frameon=False,fontsize=10,bbox_to_anchor=(.56,1.01))
fig.subplots_adjust(left=.19,right=.985,bottom=.15,top=.83,wspace=.11)
path=ROOT/'outputs/learned-pq-two-host-native.png'
fig.savefig(path,dpi=240,facecolor='white')
plt.close(fig)
print(path)
