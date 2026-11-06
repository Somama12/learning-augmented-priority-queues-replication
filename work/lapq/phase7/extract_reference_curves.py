#!/usr/bin/env python3
"""Extract plotted mean polylines from the original final PDF, not raw trials.

PDF path coordinates have six decimal places. Axes are calibrated using their
major tick marks and printed labels. A separate analytic heap curve verifies
each inverse transform; no new replication measurement is used in calibration.
"""
import hashlib
import json
import math
from pathlib import Path

from pypdf import PdfReader
from pypdf.generic import ContentStream

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'sources/original-paper.pdf'
COLORS = {'offline':(0.1215686275,0.4666666667,0.7058823529),
          'online':(1.,0.4980392157,0.0549019608),
          'dirty':(0.1725490196,0.6274509804,0.1725490196),
          'BH':(0.5490196078,0.3372549020,0.2941176471)}

def bh(n):
    return sum(math.log2(int(math.log2(k+1)))+int(math.log2(k+1))-1
               for k in range(1,n))/n

def paths(form, reader):
    color=(0.,0.,0.);clip=None;stack=[];path=[];lines=[];ticks=[]
    for a,op in ContentStream(form,reader).operations:
        if op==b'q':stack.append((color,clip))
        elif op==b'Q':color,clip=stack.pop()
        elif op==b'RG':color=tuple(float(x)for x in a)
        elif op==b'G':color=(float(a[0]),)*3
        elif op==b're':clip=tuple(float(x)for x in a)
        elif op==b'm':path=[tuple(float(x)for x in a)]
        elif op==b'l':path.append(tuple(float(x)for x in a))
        elif op in (b'S',b's',b'B',b'B*',b'f',b'f*',b'n'):
            if op in (b'S',b's') and len(path)==21 and clip:
                lines.append({'color':color,'clip':clip,'points':path})
            if op in (b'S',b's',b'B',b'B*') and len(path)==2:
                (x0,y0),(x1,y1)=path
                if abs(y1-y0)<1e-8 and abs((x0-x1)-3.5)<1e-6:
                    ticks.append((x0,y0))
            path=[]
    return lines,ticks

def main():
    reader=PdfReader(SOURCE);rows=[];checks=[];calibrations=[]
    # These figure and tick-label identities are read from page 9 of the final
    # paper. Figure 2 uses log10 ordinate; Figure 3 uses linear ordinate.
    for regime,formname,figure in [('class','/Im2',2),('decay','/Im3',3)]:
        form=reader.pages[8]['/Resources']['/XObject'][formname].get_object()
        lines,ticks=paths(form,reader)
        axes=sorted(set(tuple(l['clip'])for l in lines))
        assert len(axes)==2
        for axis,n in zip(axes,[10000,100000]):
            y=sorted(v for x,v in ticks if abs(x-axis[0])<1e-5)
            assert len(y)>=2
            if regime=='class':
                assert len(y)==2
                inverse=lambda v:10**((v-y[0])/(y[1]-y[0]))
                labels=[1.,10.]
            else:
                step=2. if n==10000 else 2.5
                inverse=lambda v:step+(v-y[0])/(y[1]-y[0])*step
                labels=[step*(i+1)for i in range(len(y))]
            calibrations.append({'regime':regime,'n':n,'page_1based':9,
                                 'figure':figure,'form':formname,'clip':axis,
                                 'tick_y':y,'tick_values':labels,
                                 'ordinate_scale':'log10'if regime=='class'else'linear'})
            for model,rgb in COLORS.items():
                found=[l for l in lines if tuple(l['clip'])==axis and
                       max(abs(a-b)for a,b in zip(l['color'],rgb))<1e-6]
                assert len(found)==1,(regime,n,model,len(found))
                line=found[0];points=line['points'];x0=points[0][0];dx=points[-1][0]-x0
                assert max(abs((p[0]-x0)/dx-i/20)for i,p in enumerate(points))<1e-7
                values=[inverse(p[1])for p in points]
                if model=='BH':
                    error=max(abs(v-bh(n))for v in values)
                    # This cross-check detects a wrong axis/color mapping. The
                    # paper's heap line need not be bit-identical to the later
                    # released formula; retain the observed discrepancy.
                    assert error/bh(n)<1e-4,(regime,n,error)
                    checks.append({'regime':regime,'n':n,'analytic_bh':bh(n),
                                   'max_abs_inverse_error':error,
                                   'relative_error':error/bh(n),'passed':True})
                else:
                    for i,v in enumerate(values):
                        rows.append({'regime':regime,'n':n,'model':model,
                                     'parameter_index':i,'mean_clean_per_key':v,
                                     'reference_kind':'PDF vector-derived plotted mean',
                                     'source_figure':figure,'source_page_1based':9})
    for regime in ['class','decay']:
        data=json.loads((HERE/f'sources/data_{regime}_1000.json').read_text())
        for model,prefix in [('offline','SL'),('online','OSL'),('dirty','DC')]:
            key=f'{prefix}_{regime}_1000'
            assert len(data['mean'][key])==len(data['std'][key])==21
            for i,(mean,std) in enumerate(zip(data['mean'][key],data['std'][key])):
                rows.append({'regime':regime,'n':1000,'model':model,
                             'parameter_index':i,'mean_clean_per_key':mean,
                             'std_clean_per_key':std,
                             'reference_kind':'Released aggregate mean/std; original trial count unknown'})
    result={'reference_rows':rows,'calibrations':calibrations,'validation':checks,
            'source_pdf_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            'source_url':'https://papers.neurips.cc/paper_files/paper/2024/file/e08e1a60c006ac3f0c9f953626b0f0c8-Paper-Conference.pdf',
            'limits':'Plotted means are digitized from rounded PDF vectors, not raw trials. Original effective trial counts and seeds are unavailable. No original standard errors are inferred.',
            'passed':len(rows)==378 and all(c['passed']for c in checks)}
    assert result['passed']
    (HERE/'published_reference_curves.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'rows':len(rows),'passed':True,'bh_inverse_checks':checks}))

if __name__=='__main__':main()
