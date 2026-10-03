import json
from html import escape

with open('result.json', encoding='utf-8') as f:
    r = json.load(f)
parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="760" viewBox="0 0 1000 760" role="img" aria-label="Paired classifier accuracy and augmentation differences">',
         '<title>Feature augmentation under correlation reversal</title>',
         '<desc>Top panels show paired test accuracies. Bottom panels show augmentation minus baseline by seed and qualifying mean plus or minus one across-seed standard error. Hollow markers denote flagged fits.</desc>',
         '<rect x="0" y="0" width="1000" height="760" fill="white"/>']
def text(x, y, s, size=14, anchor='start', color='#222222'):
    parts.append('<text x="%s" y="%s" font-family="sans-serif" font-size="%s" text-anchor="%s" fill="%s">%s</text>' % (x,y,size,anchor,color,escape(str(s))))
def line(x1,y1,x2,y2,color='#aaaaaa',width=1,dash=None):
    a = ' stroke-dasharray="%s"' % dash if dash else ''
    parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="%s"%s/>' % (x1,y1,x2,y2,color,width,a))
def circle(x,y,color,hollow=False,radius=4):
    parts.append('<circle cx="%s" cy="%s" r="%s" fill="%s" stroke="%s" stroke-width="2"/>' % (x,y,radius,'white' if hollow else color,color))
text(500,30,'Feature augmentation under correlation reversal',22,'middle')
text(500,54,'Logistic ERM | paired training and test examples | fixed synthetic setup',14,'middle')
colors = {'P':'#2166ac','Q':'#b2182b'}
for col, law in enumerate(('P','Q')):
    left, right = 80+col*490, 440+col*490
    c = r['paired_comparisons'][law]
    text((left+right)/2,85,'P: in-distribution' if law=='P' else 'Q: reversed correlation',18,'middle')
    top, bottom = 110,310
    def ay(v):
        return bottom-(bottom-top)*v
    for tick in (0,0.25,0.5,0.75,1):
        y = ay(tick)
        line(left,y,right,y,'#dddddd')
        text(left-10,y+4,'%g'%tick,12,'end')
    text(left,102,'Accuracy (proportion)',12)
    xs = [left+90,right-90]
    for i,(a,b) in enumerate(zip(c['control'],c['treatment'])):
        offset = (i-(len(c['seeds'])-1)/2)*9
        hollow = c['control_censored'][i] or c['treatment_censored'][i]
        line(xs[0]+offset,ay(a),xs[1]+offset,ay(b),'#bcbcbc')
        circle(xs[0]+offset,ay(a),'#444444',hollow)
        circle(xs[1]+offset,ay(b),colors[law],hollow)
    for x,label in zip(xs,('Baseline','Augmented')):
        text(x,334,label,14,'middle')
    text((left+right)/2,375,'Augmentation difference by seed',16,'middle')
    top2, bottom2 = 405,640
    def dy(v):
        return bottom2-(v+1)*(bottom2-top2)/2
    for tick in (-1,-0.5,0,0.5,1):
        y = dy(tick)
        line(left,y,right,y,'#777777' if tick==0 else '#dddddd',1)
        text(left-10,y+4,'%g'%tick,12,'end')
    text(left,397,'Accuracy difference (proportion)',12)
    n = len(c['seeds'])
    for i,seed in enumerate(c['seeds']):
        x = left+25+i*max(1,(right-left-100)/max(1,n-1))
        v = c['treatment'][i]-c['control'][i]
        circle(x,dy(v),colors[law],c['control_censored'][i])
        text(x,660,seed,12,'middle')
    s = r['summary'][law]
    if s['mean_difference'] is not None:
        x = right-12
        m,se = s['mean_difference'],s['standard_error']
        if se is not None:
            line(x,dy(m-se),x,dy(m+se),colors[law],3)
            line(x-6,dy(m-se),x+6,dy(m-se),colors[law],2)
            line(x-6,dy(m+se),x+6,dy(m+se),colors[law],2)
        circle(x,dy(m),colors[law],False,6)
        text(x,660,'Mean',12,'middle')
        text((left+right)/2,690,'K=%d; mean=%+.4f; SE=%s' % (s['K'],m,'undefined' if se is None else '%.4f'%se),13,'middle')
    else:
        text((left+right)/2,690,'No qualifying mean; K=0',13,'middle')
    text((left+right)/2,714,'Claimed direction: '+('above zero' if law=='P' else 'below zero'),13,'middle')
text(500,742,'Seed labels; mean bars = ±1 SE. Hollow = flagged fit. Incomplete repeats: %d; elapsed: %.2f s.' % (r['incomplete_repeats'],r['elapsed_seconds']),12,'middle')
parts.append('</svg>')
svg = '\n'.join(parts)
assert len(svg.encode('utf-8')) <= 1000000
with open('visualization.svg','w',encoding='utf-8') as f:
    f.write(svg)
