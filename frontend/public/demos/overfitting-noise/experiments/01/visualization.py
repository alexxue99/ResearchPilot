# Static SVG only; all measurements are read from result.json.
import json
import math
import xml.etree.ElementTree as ET

with open('result.json', encoding='utf-8') as f:
    r = json.load(f)
W, H = 1180, 1000
root = ET.Element('svg', {'xmlns': 'http://www.w3.org/2000/svg', 'width': str(W),
    'height': str(H), 'viewBox': '0 0 %d %d' % (W, H), 'role': 'img',
    'aria-label': 'Training-size sweep: accuracies, gaps, and paired gap changes'})
def el(tag, attrs=None, text=None, parent=root):
    e = ET.SubElement(parent, tag, {k: str(v) for k, v in (attrs or {}).items()})
    if text is not None:
        e.text = str(text)
    return e
def text(x, y, value, size=12, anchor='start', color='#263238'):
    return el('text', {'x': x, 'y': y, 'font-family': 'sans-serif', 'font-size': size,
        'text-anchor': anchor, 'fill': color}, value)
def line(x1, y1, x2, y2, color='#cfd8dc', width=1, **extra):
    a = {'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'stroke': color, 'stroke-width': width}
    a.update(extra)
    return el('line', a)
el('title', text='Fixed 65-parameter neural network with 20% independent training-label flips')
el('desc', text='Thin lines connect individual completed seed sweeps. Thick lines are arithmetic means. Training accuracy uses noisy labels; test accuracy uses clean labels. All size axes are categorical, with equal spacing, not linear sample-size axes. No uncertainty intervals are shown.')
el('rect', {'x': 0, 'y': 0, 'width': W, 'height': H, 'fill': '#ffffff'})
text(35, 32, 'Training size, noisy-label accuracy, and clean-test accuracy', 21)
text(35, 56, '65 parameters | SGD: 1200 updates per fit | test size: %s | completed sweeps: %s/%s' %
     (r['settings']['n_test'], r['completion_count'], len(r['requested_seeds'])), 13)
text(35, 77, 'Thin lines: paired seeds; thick lines: means. Smaller gaps/changes support a decreasing-gap claim.', 12)
colors = {'train_accuracy': '#1565c0', 'test_accuracy': '#d55e00',
    'absolute_gap': '#6a1b9a', 'signed_gap': '#008577',
    'absolute_baseline_contrast': '#6a1b9a', 'signed_baseline_contrast': '#008577',
    'absolute_change': '#6a1b9a', 'signed_change': '#008577'}
sizes = [str(n) for n in r['n_train_values']]
adjkeys = list(r['adjacent_changes']['absolute_change'])

def panel(index, title, keys, series, source, means, ylabel, accuracy=False):
    col, row = index % 2, index // 2
    ox, oy = 32 + col*580, 105 + row*280
    left, right, top, bottom = ox+61, ox+535, oy+53, oy+217
    text(ox, oy+17, title, 15)
    for j, (name, label) in enumerate(series):
        xx = ox + j*265
        line(xx, oy+35, xx+22, oy+35, colors[name], 3)
        text(xx+28, oy+39, label, 11)
    values = [v for name, _ in series for key in keys for v in source[name][key]]
    if accuracy:
        low, high = 0.0, 1.0
    elif not values:
        low, high = -0.1, 0.1
    elif all(v >= 0 for v in values) and all('contrast' not in name and 'change' not in name and 'signed' not in name for name, _ in series):
        low, high = 0.0, max(0.05, max(values)*1.12)
    else:
        extent = max(0.02, max(abs(v) for v in values)*1.12)
        low, high = -extent, extent
    def yy(v):
        return bottom - (v-low)/(high-low)*(bottom-top)
    def xx(k):
        return left + k*(right-left)/max(1, len(keys)-1)
    for k in range(5):
        value = low + k*(high-low)/4
        y = yy(value)
        line(left, y, right, y, '#e5e9ec', 1)
        text(left-7, y+4, '%.3f' % value, 10, 'end')
    if low <= 0 <= high:
        line(left, yy(0), right, yy(0), '#90a4ae', 1, **{'stroke-dasharray': '4 3'})
    line(left, top, left, bottom, '#546e7a')
    line(left, bottom, right, bottom, '#546e7a')
    text(ox+3, top-6, ylabel, 10)
    for k, key in enumerate(keys):
        x = xx(k)
        line(x, bottom, x, bottom+4, '#546e7a')
        text(x, bottom+19, key.replace('->', ' to '), 10, 'middle')
    text((left+right)/2, bottom+39,
         'Adjacent training sizes (examples)' if '->' in keys[0] else 'Training size (examples; categorical spacing)',
         11, 'middle')
    if not r['completion_count']:
        text((left+right)/2, (top+bottom)/2, 'No completed sweep: measurements unavailable', 12, 'middle')
        return
    for name, label in series:
        color = colors[name]
        for i, seed in enumerate(r['completed_seeds']):
            pts = [(xx(k), yy(source[name][key][i])) for k, key in enumerate(keys)]
            e = el('polyline', {'points': ' '.join('%.2f,%.2f' % p for p in pts),
                'fill': 'none', 'stroke': color, 'stroke-width': 1, 'stroke-opacity': 0.28})
            el('title', text='%s: seed %s' % (label, seed), parent=e)
            for k, (x, y) in enumerate(pts):
                e = el('circle', {'cx': x, 'cy': y, 'r': 2.2, 'fill': color, 'fill-opacity': 0.35})
                el('title', text='%s, seed %s, %s: %.6f' % (label, seed, keys[k], source[name][keys[k]][i]), parent=e)
        pts = [(xx(k), yy(means[name][key])) for k, key in enumerate(keys)]
        el('polyline', {'points': ' '.join('%.2f,%.2f' % p for p in pts), 'fill': 'none',
            'stroke': color, 'stroke-width': 3})
        for k, (x, y) in enumerate(pts):
            e = el('circle', {'cx': x, 'cy': y, 'r': 4, 'fill': color})
            el('title', text='Mean %s, %s: %.6f' % (label, keys[k], means[name][keys[k]]), parent=e)

panel(0, 'Accuracies against different label targets', sizes,
      [('train_accuracy', 'Noisy training'), ('test_accuracy', 'Clean test')], r['metrics'], r['means'], 'Accuracy (proportion)', True)
panel(1, 'Primary absolute gap', sizes, [('absolute_gap', '|training - test|')],
      r['metrics'], r['means'], 'Gap (proportion)')
panel(2, 'Secondary signed gap', sizes, [('signed_gap', 'Training - test')],
      r['metrics'], r['means'], 'Gap (proportion)')
contrast_sizes = sizes[1:]
panel(3, 'Paired contrasts vs. smallest size: absolute', contrast_sizes,
      [('absolute_baseline_contrast', 'Absolute gap minus baseline gap')], r['metrics'], r['means'], 'Difference (proportion)')
panel(4, 'Paired contrasts vs. smallest size: signed', contrast_sizes,
      [('signed_baseline_contrast', 'Signed gap minus baseline gap')], r['metrics'], r['means'], 'Difference (proportion)')
panel(5, 'Adjacent-size changes in both gap conventions', adjkeys,
      [('absolute_change', 'Absolute gap change'), ('signed_change', 'Signed gap change')],
      r['adjacent_changes'], r['adjacent_means'], 'Difference (proportion)')
text(35, 962, 'Nested training prefixes; independent flips with probability 0.2; shared test data and initialization within each seed.', 12)
text(35, 981, 'Only complete sweeps are plotted. Deadline-interrupted sweeps are discarded; finite seed means do not prove an expectation-level trend.', 11)
data = ET.tostring(root, encoding='utf-8', xml_declaration=True)
assert len(data) <= 1000000
with open('visualization.svg', 'wb') as f:
    f.write(data)
