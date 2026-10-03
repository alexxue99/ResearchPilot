# RESEARCHPILOT EXPERIMENT PLAN
# BASELINES AND METRICS
# ['Smallest training set']
# ['Noisy training and clean test accuracies, in proportions: \\(A_{s,\\mathrm{train}}(n)=\\frac{1}{n}\\sum_{i=1}^{n}\\mathbf{1}\\{h_{\\widehat{\\theta}_{s,n}}(X_{s,i})=\\widetilde{Y}_{s,i}\\}\\) and \\
# WORKING ASSUMPTIONS
# ['User-given conditions: synthetic binary classification data, fixed network size, randomly flipped training labels at a 20% rate, and clean test labels.', 'Chosen working conditions: keep the synthet
# ALGORITHM STEPS
# ['Start a wall-clock timer and configure execution using `device`, `numeric_precision`, and `compute_threads`.', 'Loop over `seeds`, stopping before a new sweep if the computation deadline has been re
# SETTINGS AND RANDOM SEEDS
# {'varied_parameters': {'n_train_values': [32, 128, 512, 2048]}, 'held_constant_settings': {'dimension': 2, 'feature_distribution': 'standard multivariate normal', 'clean_label_rule': 'first coordinate
# [11, 29, 47, 71, 101]
# EXECUTABLE STUDY
# Self-contained, scalar float64 implementation of the specified neural-network SGD.
# No noise correction, regularization, model selection, or substitute classifier.
# Reviewed papers concern other noise/training regimes and do not establish this trend.
import time
START = time.monotonic()
import math
import json
import sys

SIZES = [32, 128, 512, 2048]
SEEDS = [11, 29, 47, 71, 101]
DEADLINE = 270.0
UPDATES = 1200
BATCH = 32
WIDTH = 16
NTEST = 8192
LR = 0.05
assert sys.float_info.mant_dig == 53
# Pure Python executes CPU scalar arithmetic on one thread; no numerical libraries.

def check_deadline():
    if time.monotonic() - START >= DEADLINE:
        raise TimeoutError('computation deadline')

# SeedSequence's default four-word entropy pool and child spawn keys, implemented
# directly to avoid a dependency on NumPy. Arithmetic here is unsigned uint32.
MASK32 = (1 << 32) - 1
MASK64 = (1 << 64) - 1
MASK128 = (1 << 128) - 1

def seedsequence_words(entropy, spawn_key, count=8):
    ent = []
    while entropy:
        ent.append(entropy & MASK32)
        entropy >>= 32
    if not ent:
        ent = [0]
    if spawn_key and len(ent) < 4:
        ent += [0] * (4 - len(ent))
    ent += list(spawn_key)
    hc = 0x43b0d7e5
    def hashmix(value):
        nonlocal hc
        value = (value ^ hc) & MASK32
        hc = (hc * 0x931e8875) & MASK32
        value = (value * hc) & MASK32
        return value ^ (value >> 16)
    def mix(x, y):
        value = (0xca01f9dd * x - 0x4973f715 * y) & MASK32
        return value ^ (value >> 16)
    pool = [hashmix(ent[i] if i < len(ent) else 0) for i in range(4)]
    for src in range(4):
        for dst in range(4):
            if src != dst:
                pool[dst] = mix(pool[dst], hashmix(pool[src]))
    for value in ent[4:]:
        for dst in range(4):
            pool[dst] = mix(pool[dst], hashmix(value))
    hc = 0x8b51f9dd
    out = []
    for i in range(count):
        value = pool[i % 4] ^ hc
        hc = (hc * 0x58f38ded) & MASK32
        value = (value * hc) & MASK32
        out.append(value ^ (value >> 16))
    return out

class PCG64:
    # PCG XSL-RR 128/64 (not PCG64DXSM). Each stream has a SeedSequence child.
    MULT = 0x2360ed051fc65da44385df649fccf645
    def __init__(self, seed, child):
        words = seedsequence_words(seed, (child,))
        u = [words[2*i] | (words[2*i+1] << 32) for i in range(4)]
        initstate = (u[0] << 64) | u[1]
        initseq = (u[2] << 64) | u[3]
        self.inc = ((initseq << 1) | 1) & MASK128
        self.state = 0
        self.step()
        self.state = (self.state + initstate) & MASK128
        self.step()
        self.spare = None
    def step(self):
        self.state = (self.state * self.MULT + self.inc) & MASK128
    def next64(self):
        self.step()
        hi = self.state >> 64
        value = (hi ^ (self.state & MASK64)) & MASK64
        rot = hi >> 58
        return ((value >> rot) | (value << ((-rot) & 63))) & MASK64
    def uniform(self):
        return (self.next64() >> 11) * (1.0 / (1 << 53))
    def normal(self):
        # Box-Muller transform of PCG64 uniforms; this intentionally does not
        # depend on a library-specific normal sampler or its lookup tables.
        if self.spare is not None:
            value, self.spare = self.spare, None
            return value
        u = 1.0 - self.uniform()
        angle = 2.0 * math.pi * self.uniform()
        radius = math.sqrt(-2.0 * math.log(u))
        self.spare = radius * math.sin(angle)
        return radius * math.cos(angle)
    def randbelow(self, n):
        # Rejection prevents modulo bias.
        limit = (1 << 64) - ((1 << 64) % n)
        while True:
            value = self.next64()
            if value < limit:
                return value % n
    def batch(self, n, k):
        # Partial Fisher-Yates shuffle via a sparse map; uniform distinct sample.
        swaps = {}
        result = []
        for i in range(k):
            j = i + self.randbelow(n - i)
            chosen = swaps.get(j, j)
            swaps[j] = swaps.get(i, i)
            result.append(chosen)
        return result

def features(rng, n):
    result = []
    for i in range(n):
        if i % 256 == 0:
            check_deadline()
        result.append((rng.normal(), rng.normal()))
    return result

def initialize(rng):
    # W has shape (16,2); a, v have length 16; c is scalar. Total = 65.
    w0, w1 = [], []
    for j in range(WIDTH):
        w0.append(0.1 * rng.normal())
        w1.append(0.1 * rng.normal())
    return w0, w1, [0.0]*WIDTH, [0.1*rng.normal() for _ in range(WIDTH)], 0.0

def fit(initial, x, labels, n, rng, progress):
    w0, w1, a, v = [p[:] for p in initial[:4]]
    c = initial[4]
    tanh, exp, log1p = math.tanh, math.exp, math.log1p
    last_loss = None
    for step in range(UPDATES):
        check_deadline()
        indices = rng.batch(n, BATCH)
        gw0, gw1, ga, gv = ([0.0]*WIDTH for _ in range(4))
        gc, loss = 0.0, 0.0
        for i in indices:
            x0, x1 = x[i]
            y = labels[i]
            hidden = [tanh(w0[j]*x0 + w1[j]*x1 + a[j]) for j in range(WIDTH)]
            z = c
            for j in range(WIDTH):
                z += v[j]*hidden[j]
            # BCE = softplus(z) - y*z, evaluated without overflow.
            loss += max(z, 0.0) - y*z + log1p(exp(-abs(z)))
            if z >= 0.0:
                p = 1.0 / (1.0 + exp(-z))
            else:
                ez = exp(z)
                p = ez / (1.0 + ez)
            dz = p - y
            gc += dz
            for j in range(WIDTH):
                h = hidden[j]
                dh = dz * v[j] * (1.0 - h*h)
                gv[j] += dz*h
                ga[j] += dh
                gw0[j] += dh*x0
                gw1[j] += dh*x1
        rate = LR / BATCH
        # All gradients used the old parameter vector, then one SGD update.
        for j in range(WIDTH):
            w0[j] -= rate*gw0[j]
            w1[j] -= rate*gw1[j]
            a[j] -= rate*ga[j]
            v[j] -= rate*gv[j]
        c -= rate*gc
        last_loss = loss / BATCH
        progress['total_updates_executed'] += 1
        progress['updates_in_current_fit'] = step + 1
    return (w0, w1, a, v, c), last_loss

def evaluate(parameters, x, labels, n):
    check_deadline()
    w0, w1, a, v, c = parameters
    correct = 0
    loss = 0.0
    tanh = math.tanh
    for i in range(n):
        if i % 256 == 0:
            check_deadline()
        x0, x1 = x[i]
        z = c
        for j in range(WIDTH):
            z += v[j] * tanh(w0[j]*x0 + w1[j]*x1 + a[j])
        correct += int(int(z >= 0.0) == labels[i])
        loss += max(z, 0.0) - labels[i]*z + math.log1p(math.exp(-abs(z)))
    check_deadline()
    return correct, correct / n, loss / n

settings = {
    'dimension': 2, 'feature_distribution': 'standard multivariate normal',
    'clean_label_rule': 'first coordinate nonnegative', 'flip_probability': 0.2,
    'n_test': NTEST, 'architecture': {'hidden_width': 16, 'hidden_activation': 'tanh',
        'output_activation': 'sigmoid', 'parameter_count': 65},
    'initialization': {'weight_distribution': 'independent zero-mean normal',
        'weight_standard_deviation': 0.1, 'bias_value': 0},
    'preprocessing': 'none', 'optimizer': 'mini-batch SGD on mean binary cross-entropy',
    'batch_size': BATCH, 'batch_sampling': 'uniform without replacement within update; independent across updates',
    'learning_rate': LR, 'updates_per_fit': UPDATES, 'training_budget': 'equal update counts',
    'coupling': 'nested training prefixes; shared clean test set and initialization; independent batch streams by size',
    'random_generator': 'PCG64 with SeedSequence child streams',
    'normal_sampler': 'Box-Muller with cached second variate',
    'stream_order': ['training_features', 'training_flips', 'test_features', 'initialization', 'batches_in_training_size_order'],
    'device': 'CPU', 'numeric_precision': 'float64', 'compute_threads': 1,
    'compute_deadline_seconds': 270, 'max_experiment_seconds': 300,
    'maximum_fits': 20, 'maximum_total_updates': 24000,
    'stopping_rule': '1200 updates per fit; discard interrupted seed sweep; no early stopping or model selection'
}
completed = []
status = []
progress = {'total_updates_executed': 0, 'fits_started': 0, 'fits_finished': 0,
            'updates_in_current_fit': 0, 'current_size': None}
stop = False
for seed in SEEDS:
    if stop or time.monotonic() - START >= DEADLINE:
        status.append({'seed': seed, 'status': 'not_started_deadline', 'censored': True})
        stop = True
        continue
    sweep_start = time.monotonic()
    progress['current_size'] = None
    progress['updates_in_current_fit'] = 0
    fits_this_seed = 0
    try:
        # Spawn in the exact specified order: four data/model children, then
        # one child per training size. No global RNG is used.
        streams = [PCG64(seed, i) for i in range(4 + len(SIZES))]
        pool = features(streams[0], max(SIZES))
        clean = [int(x[0] >= 0.0) for x in pool]
        flips = [int(streams[1].uniform() < 0.2) for _ in pool]
        noisy = [y ^ b for y, b in zip(clean, flips)]
        test = features(streams[2], NTEST)
        test_labels = [int(x[0] >= 0.0) for x in test]
        initial = initialize(streams[3])
        fits = []
        for k, n in enumerate(SIZES):
            check_deadline()
            progress['current_size'] = n
            progress['updates_in_current_fit'] = 0
            progress['fits_started'] += 1
            params, batch_loss = fit(initial, pool, noisy, n, streams[4+k], progress)
            check_deadline()
            train_correct, atrain, train_loss = evaluate(params, pool, noisy, n)
            test_correct, atest, test_loss = evaluate(params, test, test_labels, NTEST)
            signed = atrain - atest
            fits.append({'n': n, 'train_correct': train_correct, 'test_correct': test_correct,
                'train_accuracy': atrain, 'test_accuracy': atest,
                'absolute_gap': abs(signed), 'signed_gap': signed,
                'flip_count': sum(flips[:n]), 'realized_flip_fraction': sum(flips[:n])/n,
                'train_bce': train_loss, 'test_bce': test_loss,
                'last_minibatch_bce_before_update': batch_loss, 'updates': UPDATES})
            fits_this_seed += 1
            progress['fits_finished'] += 1
        for f in fits:
            for convention in ('absolute', 'signed'):
                key = convention + '_gap'
                f[convention + '_baseline_contrast'] = f[key] - fits[0][key]
        adjacent = []
        for k in range(len(SIZES)-1):
            adjacent.append({'from_n': SIZES[k], 'to_n': SIZES[k+1],
                'absolute_change': fits[k+1]['absolute_gap'] - fits[k]['absolute_gap'],
                'signed_change': fits[k+1]['signed_gap'] - fits[k]['signed_gap']})
        check_deadline()
        completed.append({'seed': seed, 'fits': fits, 'adjacent_changes': adjacent})
        status.append({'seed': seed, 'status': 'complete', 'censored': False,
                       'elapsed_seconds': time.monotonic()-sweep_start})
    except TimeoutError:
        status.append({'seed': seed, 'status': 'interrupted_discarded', 'censored': True,
            'finished_fits_discarded': fits_this_seed, 'interrupted_size': progress['current_size'],
            'updates_in_current_fit': progress['updates_in_current_fit'],
            'elapsed_seconds': time.monotonic()-sweep_start})
        stop = True

seed_ids = [s['seed'] for s in completed]
count = len(completed)
metric_names = ['train_accuracy', 'test_accuracy', 'absolute_gap', 'signed_gap',
                'absolute_baseline_contrast', 'signed_baseline_contrast']
metrics, means = {}, {}
for name in metric_names:
    metrics[name] = {str(n): [s['fits'][k][name] for s in completed] for k, n in enumerate(SIZES)}
    means[name] = {str(n): (sum(v)/count if count else None) for n, v in metrics[name].items()}
adjacent, adjacent_means = {}, {}
for convention in ('absolute', 'signed'):
    name = convention + '_change'
    adjacent[name] = {str(SIZES[k]) + '->' + str(SIZES[k+1]):
        [s['adjacent_changes'][k][name] for s in completed] for k in range(len(SIZES)-1)}
    adjacent_means[name] = {key: (sum(v)/count if count else None) for key, v in adjacent[name].items()}

comparisons = {}
for convention in ('absolute', 'signed'):
    key = convention + '_gap'
    # Smaller treatment gap supports the conjecture under either convention.
    control = {str(n): metrics[key][str(SIZES[0])][:] for n in SIZES[1:]}
    treatment = {str(n): metrics[key][str(n)][:] for n in SIZES[1:]}
    flags = {str(n): [False]*count for n in SIZES[1:]}
    comparisons[convention] = {'control': control, 'treatment': treatment,
        'higher_supports': False, 'paired_seeds': seed_ids,
        'control_censored': flags, 'treatment_censored': flags,
        'baseline_n': SIZES[0], 'metric': key}

trend_summary = {}
for convention in ('absolute', 'signed'):
    name = convention + '_change'
    mean_values = list(adjacent_means[name].values())
    trend_summary[convention] = {
        'strictly_decreasing_mean_at_every_adjacent_size': all(v < 0 for v in mean_values) if count else None,
        'strictly_decreasing_individual_seed_sweeps':
            sum(all(a[name] < 0 for a in s['adjacent_changes']) for s in completed),
        'nondecreasing_adjacent_changes_per_seed':
            [sum(a[name] >= 0 for a in s['adjacent_changes']) for s in completed]
    }
result = {
    'name': 'Training-size sweep with independent label flips',
    'n_train_values': SIZES, 'requested_seeds': SEEDS, 'completed_seeds': seed_ids,
    'completion_count': count, 'seed_status': status, 'settings': settings,
    'units': 'accuracies and gaps are proportions; contrasts and changes are proportion differences; BCE is nats/example',
    'raw_seed_sweeps': completed, 'metrics': metrics, 'means': means,
    'adjacent_changes': adjacent, 'adjacent_means': adjacent_means,
    'control': comparisons['absolute']['control'], 'treatment': comparisons['absolute']['treatment'],
    'higher_supports': False, 'paired_seeds': seed_ids,
    'control_censored': comparisons['absolute']['control_censored'],
    'treatment_censored': comparisons['absolute']['treatment_censored'],
    'comparisons': comparisons, 'trend_summary': trend_summary,
    'work_executed': progress, 'elapsed_seconds': time.monotonic()-START,
    'interpretation_notes': [
        'Primary gap is absolute; secondary gap is noisy training minus clean test accuracy.',
        'Flips are independent with probability 0.2, not an exactly 20 percent subset.',
        'All measurements and paired arrays include only fully completed seed sweeps.',
        'Deadline-related censoring may be informative; seed_status also includes excluded seeds.',
        'A finite five-seed sweep does not establish an expectation-level or universal monotonicity claim.',
        'Reviewed papers do not test this exact sample-size/gap setting; their methods are not substituted for the specified SGD.'
    ]
}
payload = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
if len(payload) > 100000:
    raise RuntimeError('result.json exceeds size limit')
with open('result.json', 'wb') as f:
    f.write(payload)
