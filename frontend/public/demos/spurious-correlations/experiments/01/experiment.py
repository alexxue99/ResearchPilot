# RESEARCHPILOT EXPERIMENT PLAN
# BASELINES AND METRICS
# ['Original-feature logistic classifier']
# ['Test accuracy, in proportions: \\(\\widehat{A}_{D,r}(\\widehat{f}_j)=\\frac{1}{N}\\sum_{i=1}^{N}\\mathbf{1}\\{\\widehat{f}_j(z_{D,r,i}^{(j)})=Y_{D,r,i}\\}\\), where \\(D\\in\\{P,Q\\}\\), \\(j\\in\\{
# WORKING ASSUMPTIONS
# ['User-given conditions: the task is binary classification on synthetic data; an added feature is strongly label-correlated during training; its label correlation reverses at test time; and the compar
# ALGORITHM STEPS
# ['Start a wall-clock timer and process `seeds` in their listed order, checking the global time cap before each fit update and test evaluation.', 'For each seed, initialize an independent random-number
# SETTINGS AND RANDOM SEEDS
# {'varied_parameters': {}, 'held_constant_settings': {'mu': 1, 'p': 0.95, 'lambda': 0.01, 'n_train': 2000, 'n_test': 10000, 'repeat_count': 5, 'initial_parameter': 0, 'gradient_tolerance': 1e-06, 'iter
# [17, 43, 101, 229, 503]
# EXECUTABLE STUDY
import json
import math
import random
import time

# Fixed operational test: regularized logistic ERM, not group DRO.
# The resource-limited five repeats supersede the inherited nominal R=30.
START = time.perf_counter()
CAP = 300.0
SEEDS = [17, 43, 101, 229, 503]
SETTINGS = dict(mu=1, p=0.95, **{'lambda': 0.01}, n_train=2000,
                n_test=10000, repeat_count=5, initial_parameter=0,
                gradient_tolerance=1e-6, iteration_cap_per_fit=100000,
                training_law='P', test_laws=['P', 'Q'], arithmetic='float64')

class TimeCap(Exception):
    pass

def check():
    if time.perf_counter() - START >= CAP:
        raise TimeCap()

def sample(rng, n, p):
    data = []
    for i in range(n):
        if i % 128 == 0:
            check()
        y = 1.0 if rng.random() < 0.5 else -1.0
        eps = rng.gauss(0.0, 1.0)
        b = rng.random() < p
        data.append((y + eps, y * (1.0 if b else -1.0), y))
    return data

def largest_eigenvalue(a):
    # Symmetric Jacobi diagonalization of the 2x2 or 3x3 Gram matrix.
    a = [row[:] for row in a]
    d = len(a)
    for sweep in range(100):
        check()
        p, q = max(((i, j) for i in range(d) for j in range(i+1, d)),
                   key=lambda ij: abs(a[ij[0]][ij[1]]))
        off = a[p][q]
        if abs(off) <= 1e-14 * max(1.0, max(abs(a[i][i]) for i in range(d))):
            return max(a[i][i] for i in range(d))
        tau = (a[q][q] - a[p][p]) / (2.0 * off)
        t = math.copysign(1.0, tau) / (abs(tau) + math.hypot(1.0, tau))
        c = 1.0 / math.sqrt(1.0 + t*t)
        s = t*c
        app, aqq = a[p][p], a[q][q]
        a[p][p] = app - t*off
        a[q][q] = aqq + t*off
        a[p][q] = a[q][p] = 0.0
        for k in range(d):
            if k != p and k != q:
                akp, akq = a[k][p], a[k][q]
                a[k][p] = a[p][k] = c*akp - s*akq
                a[k][q] = a[q][k] = s*akp + c*akq
    raise ArithmeticError('Jacobi diagonalization did not converge')

def logistic_factor(m):
    if m >= 0.0:
        e = math.exp(-m)
        return e / (1.0 + e)
    return 1.0 / (1.0 + math.exp(m))

def fit(data, augmented):
    d = 3 if augmented else 2
    theta = [0.0]*d
    diag = dict(coefficients=theta, gradient_norm=None, gradient_updates=0,
                converged=False, iteration_cap_status=False, time_cap_status=False,
                status='initializing', step_size=None, squared_spectral_norm=None)
    try:
        check()
        gram = [[0.0]*d for _ in range(d)]
        for x, s, y in data:
            z = [1.0, x, s][:d]
            for k in range(d):
                for l in range(d):
                    gram[k][l] += z[k]*z[l]
        eig = largest_eigenvalue(gram)
        eta = 1.0 / (0.01 + eig/(4.0*len(data)))
        diag.update(step_size=eta, squared_spectral_norm=eig)
        updates = 0
        while True:
            check()
            a, b = theta[:2]
            c = theta[2] if augmented else 0.0
            g0 = g1 = g2 = 0.0
            for x, s, y in data:
                u = -y * logistic_factor(y*(a + b*x + c*s))
                g0 += u
                g1 += u*x
                if augmented:
                    g2 += u*s
            n = len(data)
            g = [g0/n + 0.01*a, g1/n + 0.01*b]
            if augmented:
                g.append(g2/n + 0.01*c)
            norm = math.sqrt(sum(v*v for v in g))
            diag['gradient_norm'] = norm
            if norm <= 1e-6:
                diag.update(converged=True, status='converged')
                break
            if updates >= 100000:
                diag.update(iteration_cap_status=True, status='iteration_cap')
                break
            check()  # Mandatory global-cap check before every gradient update.
            theta = [v - eta*w for v, w in zip(theta, g)]
            updates += 1
            diag.update(coefficients=theta, gradient_updates=updates, gradient_norm=None)
    except TimeCap:
        diag.update(time_cap_status=True, status='time_cap')
    return diag

def evaluate(data, fits):
    check()
    counts = [0, 0]
    # Discordance counts retain information about within-test pairing.
    joint = [[0, 0], [0, 0]]
    t0, t1 = [f['coefficients'] for f in fits]
    for i, (x, s, y) in enumerate(data):
        if i % 128 == 0:
            check()
        c0 = int((1.0 if t0[0] + t0[1]*x >= 0 else -1.0) == y)
        c1 = int((1.0 if t1[0] + t1[1]*x + t1[2]*s >= 0 else -1.0) == y)
        counts[0] += c0
        counts[1] += c1
        joint[c0][c1] += 1
    check()
    acc = [v/len(data) for v in counts]
    return dict(n=len(data), correct_counts=counts, accuracy=acc,
                difference=acc[1]-acc[0], correctness_table=joint)

repeats = []
for seed in SEEDS:
    rec = dict(seed=seed, completed=False, qualifying=False, fits=[], tests={})
    try:
        check()
        repeats.append(rec)
        rng = random.Random(seed)
        train = sample(rng, 2000, 0.95)
        for augmented in (False, True):
            check()
            f = fit(train, augmented)
            f['model'] = 'augmented' if augmented else 'baseline'
            rec['fits'].append(f)
            if f['time_cap_status']:
                raise TimeCap()
        # Both test samples are fresh and independent, using the same seed stream.
        tests = {'P': sample(rng, 10000, 0.95), 'Q': sample(rng, 10000, 0.05)}
        for law in ('P', 'Q'):
            check()
            rec['tests'][law] = evaluate(tests[law], rec['fits'])
        rec['completed'] = True
        rec['qualifying'] = all(f['converged'] for f in rec['fits'])
        rec['status'] = 'completed' if rec['qualifying'] else 'completed_flagged_fit'
    except TimeCap:
        if rec not in repeats:
            repeats.append(rec)
        rec['status'] = 'incomplete_time_cap'
        break

qualified = [r for r in repeats if r['qualifying']]
completed = [r for r in repeats if r['completed']]
summary = {}
comparisons = {}
for law in ('P', 'Q'):
    values = [r['tests'][law]['difference'] for r in qualified]
    k = len(values)
    mean = sum(values)/k if k else None
    se = math.sqrt(sum((v-mean)**2 for v in values)/(k*(k-1))) if k > 1 else None
    summary[law] = dict(K=k, mean_difference=mean, standard_error=se,
                        qualifying_seeds=[r['seed'] for r in qualified], differences=values)
    comparisons[law] = dict(seeds=[r['seed'] for r in completed],
        control=[r['tests'][law]['accuracy'][0] for r in completed],
        treatment=[r['tests'][law]['accuracy'][1] for r in completed],
        higher_supports=(law == 'P'),
        control_censored=[not r['qualifying'] for r in completed],
        treatment_censored=[not r['qualifying'] for r in completed],
        censoring_meaning='True denotes a completed evaluation with at least one nonconverged fit; no accuracy imputation.')

result = dict(design='Feature augmentation under correlation reversal',
    method='Full-batch gradient descent for L2-regularized logistic ERM; intercept also regularized',
    settings=SETTINGS, seeds=SEEDS, wall_clock_cap_seconds=CAP,
    paired_comparisons=comparisons, repeats=repeats, summary=summary,
    flagged_completed_seeds=[r['seed'] for r in completed if not r['qualifying']],
    incomplete_repeats=sum(not r['completed'] for r in repeats),
    unstarted_seeds=[s for s in SEEDS if s not in [r['seed'] for r in repeats]],
    elapsed_seconds=time.perf_counter()-START,
    scope='One fixed synthetic setup; not a universal test over classifier families or generators.',
    correctness_table_axes=['baseline correct: 0,1', 'augmented correct: 0,1'])
blob = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
assert len(blob.encode('utf-8')) <= 100000
with open('result.json', 'w', encoding='utf-8') as f:
    f.write(blob)
