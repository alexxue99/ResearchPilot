# Static stdlib SVG; measurements are loaded only from result.json.
import json, math
from xml.sax.saxutils import escape
with open('result.json',encoding='utf-8') as f: r = json.load(f)
W,H = 1040,740
parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1040" height="740" viewBox="0 0 1040 740" role="img" aria-label="Random Fourier feature regression test error curves">',
         '<title>Fourier-feature interpolation and fixed ridge comparison</title>',
         '<desc>Mean test MSE over completed seed repetitions. Vertical axis is logarithmic. Faint curves show individual repetitions. Window statistics use maxima of averaged curves.</desc>',
         '<rect x="0" y="0" width="1040" height="740" fill="white"/>']
def text(x,y,t,size=14,color='#222',anchor='start'):
    parts.append('<text x="%g" y="%g" font-family="sans-serif" font-size="%g" fill="%s" text-anchor="%s">%s</text>'%(x,y,size,color,anchor,escape(str(t))))
def line(x1,y1,x2,y2,color='#bbb',width=1,dash=None):
    extra = ' stroke-dasharray="%s"'%dash if dash else ''
    parts.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s" stroke-width="%g"%s/>'%(x1,y1,x2,y2,color,width,extra))
text(65,32,'Random Fourier features: interpolation peak and ridge',23)
S = r['completed_repetition_count']
text(65,57,'Completed repetitions: %s / %s; n_train = %s; independent noisy test labels'%(S,len(r['requested_seeds']),r['settings']['n_train']),14)
if not S:
    text(65,120,'No completed repetitions: no measured curves are available.',18)
else:
    grid = r['settings']['feature_counts']
    curves = r['average_test_mse']
    keys = ['0','0.0001','0.001','0.01']
    colors = ['#222222','#0072b2','#d55e00','#009e73']
    allvals = [v for rec in r['repetitions'] for arr in rec['test_mse'].values() for v in arr]
    # A log axis requires strictly positive MSE; measured MSE is positive here.
    positive = [v for v in allvals if v > 0]
    if not positive: raise ValueError('Log-axis plot requires positive measured MSE')
    lo = math.floor(math.log10(min(positive)))
    hi = math.ceil(math.log10(max(positive)))
    if hi == lo: hi += 1
    x0,y0,pw,ph = 90,90,740,410
    def X(m): return x0+(m-min(grid))/(max(grid)-min(grid))*pw
    def Y(v): return y0+ph-(math.log10(v)-lo)/(hi-lo)*ph
    wm = r['window_metrics']
    win = wm['window']
    if win:
        xa,xb = X(min(win)),X(max(win))
        parts.append('<rect x="%g" y="%g" width="%g" height="%g" fill="#eee9bf" fill-opacity="0.65"/>'%(xa,y0,max(xb-xa,1),ph))
    for exp in range(lo,hi+1):
        yy = Y(10.0**exp)
        line(x0,yy,x0+pw,yy,'#dddddd')
        text(x0-10,yy+5,'%g'%(10.0**exp),12,anchor='end')
    for m in grid:
        xx = X(m)
        line(xx,y0+ph,xx,y0+ph+5,'#555')
        text(xx,y0+ph+20,m,10,anchor='middle')
    line(x0,y0,x0,y0+ph,'#555')
    line(x0,y0+ph,x0+pw,y0+ph,'#555')
    text(x0+pw/2,547,'Feature count m (features)',15,anchor='middle')
    parts.append('<text x="25" y="295" transform="rotate(-90 25 295)" font-family="sans-serif" font-size="14" fill="#222" text-anchor="middle">Test MSE (squared-label units; log scale)</text>')
    med = wm['median_interpolation_threshold']
    if med is not None:
        xx = X(med)
        line(xx,y0,xx,y0+ph,'#777',1.5,'5 4')
        text(xx+6,y0+18,'median interpolation: %g'%med,12)
    for key,color in zip(keys,colors):
        for rec in r['repetitions']:
            vals = rec['test_mse'][key]
            points = ' '.join('%g,%g'%(X(m),Y(v)) for m,v in zip(grid,vals) if v>0)
            parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="1" stroke-opacity="0.18"/>'%(points,color))
        vals = curves[key]
        points = ' '.join('%g,%g'%(X(m),Y(v)) for m,v in zip(grid,vals) if v>0)
        parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2.5"/>'%(points,color))
        for m,v in zip(grid,vals):
            if v>0: parts.append('<circle cx="%g" cy="%g" r="3" fill="%s"/>'%(X(m),Y(v),color))
    for i,(key,color) in enumerate(zip(keys,colors)):
        yy = 123+i*38
        line(850,yy,879,yy,color,3)
        text(886,yy+5,'LS' if key=='0' else 'ridge '+key,13)
    text(850,300,'Bold: means',12)
    text(850,320,'Faint: seeds',12)
    text(850,345,'Shaded: window',12)
    text(65,580,'Numerical thresholds by seed: '+', '.join('%s: %s'%(rec['seed'],rec['threshold']) for rec in r['repetitions']),13)
    if wm['available']:
        text(65,605,'Window: %s; adjacent flanks: %s, %s features'%(win,wm['left_flank'],wm['right_flank']),13)
        text(65,630,'Unregularized peak P0 = %.6g; prominence C0 = %.6g squared-label units'%(wm['peak_mse']['0'],wm['unregularized_peak_prominence']),13)
        reductions = '; '.join('lambda %s: %.6g'%(k,wm['ridge_peak_reduction'][k]) for k in keys[1:])
        text(65,655,'Peak reductions D_lambda = P0 - P_lambda: '+reductions,13)
    else:
        text(65,615,'Window metrics unavailable: '+wm.get('unavailable_reason',''),12)
text(65,701,'Fixed ridge parameters, not optimized on test data. One finite setting does not establish a universal claim.',12)
parts.append('</svg>')
svg = '\n'.join(parts)
assert len(svg.encode('utf-8')) <= 1000000
with open('visualization.svg','w',encoding='utf-8') as f: f.write(svg)
