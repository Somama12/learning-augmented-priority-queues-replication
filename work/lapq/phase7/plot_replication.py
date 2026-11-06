#!/usr/bin/env python3
"""Plot audited comparison counts and published references; no experiments."""
import json
import os
from pathlib import Path

HERE=Path(__file__).resolve().parent
os.environ.setdefault('MPLCONFIGDIR',str(HERE/'mpl-cache'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np

def main():
    s=json.loads((HERE/'replication_summary.json').read_text())
    assert s['complete'] and s['audit']['passed']
    out=HERE/'figures';out.mkdir(exist_ok=True)
    colors={'offline':'#286aa6','online':'#c96b12','dirty':'#27824d'}
    labels={'offline':'Offline advice','online':'Online advice','dirty':'Dirty/clean'}
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,
                         'axes.spines.top':False,'axes.spines.right':False})
    files=[]
    for regime in ('class','decay'):
        fig,axes=plt.subplots(1,2,figsize=(7.4,3.3),sharex=True)
        for ax,n in zip(axes,[10000,100000]):
            for model in colors:
                rows=sorted([r for r in s['point_rows']if (r['regime'],r['n'],r['model'])==(regime,n,model)],key=lambda r:r['parameter_index'])
                assert len(rows)==21
                x=np.arange(21)/20
                ax.fill_between(x,[r['ci95_low']for r in rows],[r['ci95_high']for r in rows],color=colors[model],alpha=.15,linewidth=0)
                ax.plot(x,[r['mean_clean_per_key']for r in rows],color=colors[model],linewidth=1.7,marker='o',markersize=3.8,markerfacecolor='white',markeredgewidth=.9,zorder=3)
                ax.plot(x,[r['reference_mean_clean_per_key']for r in rows],color=colors[model],linewidth=1.15,linestyle=(0,(4,2)),alpha=.9,zorder=2)
            ax.axhline(s['analytic_bh_per_key'][str(n)],color='#525252',linestyle=':',linewidth=1.3,zorder=1)
            if regime=='class':ax.set_yscale('log')
            ax.set_title(f'n = {n:,}',fontsize=12)
            ax.set_xticks([0,.25,.5,.75,1])
            ax.set_xlabel('Class parameter / n'if regime=='class'else'Decay steps / floor(n sqrt(n))',fontsize=10)
            ax.grid(alpha=.18);ax.set_axisbelow(True)
        axes[0].set_ylabel('Clean comparisons per key',fontsize=11)
        handles=[Line2D([0],[0],color=colors[m],lw=2,label=labels[m])for m in colors]
        handles += [Line2D([0],[0],color='#333333',lw=1.7,marker='o',markersize=4,markerfacecolor='white',label='Replication'),
                    Line2D([0],[0],color='#333333',lw=1.2,ls='--',label='Published mean'),
                    Line2D([0],[0],color='#525252',lw=1.3,ls=':',label='Analytic heap')]
        fig.legend(handles=handles,loc='upper center',ncol=3,frameon=False,fontsize=9.1,bbox_to_anchor=(.5,1.01))
        fig.tight_layout(rect=[0,0,1,.84],w_pad=1.7)
        for ext in ('pdf','png'):
            f=out/f'replication-{regime}.{ext}';fig.savefig(f,dpi=300,bbox_inches='tight');files.append(str(f.relative_to(HERE)))
        plt.close(fig)
    (out/'figure_manifest.json').write_text(json.dumps({'files':files,'data':'replication_summary.json','coordinate_offsets':False,'uncertainty':'Pointwise95% bootstrap intervals of our30replicates; published curves have no inferred standard errors.'},indent=2)+'\n')
    print(json.dumps({'figures':files}))

if __name__=='__main__':main()
