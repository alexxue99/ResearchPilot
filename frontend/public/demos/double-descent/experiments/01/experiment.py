# RESEARCHPILOT EXPERIMENT PLAN
# BASELINES AND METRICS
# ['Minimum-norm least squares']
# ['Test MSE, in squared-label units: \\(E_{s,\\lambda}(m)=\\frac{1}{n_{\\text{test}}}\\sum_{i=1}^{n_{\\text{test}}}\\left(\\phi_{s,m}(x_{\\text{test},s,i})^\\top\\widehat{a}_{s,m,\\lambda}-y_{\\text{te
# WORKING ASSUMPTIONS
# ['User-given conditions: synthetic regression has observation noise; the fitted representation uses random Fourier features; feature count varies; the comparison concerns test error and ridge regulari
# ALGORITHM STEPS
# ['Start the wall-clock timer and enforce `linear_algebra_threads` and `arithmetic`.', 'Loop over `seeds`, checking the work-cap deadline before each expensive operation; discard an unfinished repetiti
# SETTINGS AND RANDOM SEEDS
# {'held_constant': {'n_train': 60, 'n_test': 2000, 'd': 6, 'input_distribution': 'standard multivariate Gaussian', 'target': 'first input coordinate', 'noise_variance': 0.2, 'kernel_bandwidth': 2.0, 'p
# [17, 43, 89, 131, 197]
# EXECUTABLE STUDY
# Pure-stdlib float64 random Fourier regression experiment.
# Direct Golub-Reinsch thin SVD (bidiagonalization + implicit QR), not
# normal-equation eigendecomposition. Fixed ridge values; no test selection.
import time
START = time.monotonic()
DEADLINE = START + 280.0
import os
for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','BLIS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[name] = '1'
import math, random, json, statistics, struct, sys
assert struct.calcsize('d') == 8 and sys.float_info.mant_dig == 53
SEEDS = [17,43,89,131,197]
GRID = [15,30,45,54,57,60,63,66,75,90,120,180]
LAMBDAS = [0.0,0.0001,0.001,0.01]
KEYS = ['0','0.0001','0.001','0.01']
N, NT, DIM, MAXM = 60, 2000, 6, 180
RHO, TOL = 1e-12, 1e-8
class Deadline(Exception): pass
def check():
    if time.monotonic() >= DEADLINE: raise Deadline()
def dot(a,b): return sum(x*y for x,y in zip(a,b))
def sign(a,b): return abs(a) if b >= 0.0 else -abs(a)
def transpose(a): return [list(c) for c in zip(*a)]

def tall_svd(a):
    # Golub-Reinsch SVD for rows >= columns; returns U, singular values, V.
    a = [row[:] for row in a]
    nr, nc = len(a), len(a[0])
    w = [0.0]*nc
    rv = [0.0]*nc
    v = [[0.0]*nc for _ in range(nc)]
    g = scale = anorm = 0.0
    for i in range(nc):
        check()
        l = i+1
        rv[i] = scale*g
        g = scale = s = 0.0
        scale = sum(abs(a[k][i]) for k in range(i,nr))
        if scale:
            for k in range(i,nr):
                a[k][i] /= scale
                s += a[k][i]*a[k][i]
            f = a[i][i]
            g = -sign(math.sqrt(s),f)
            h = f*g-s
            a[i][i] = f-g
            for j in range(l,nc):
                s = sum(a[k][i]*a[k][j] for k in range(i,nr))
                f = s/h
                for k in range(i,nr): a[k][j] += f*a[k][i]
            for k in range(i,nr): a[k][i] *= scale
        w[i] = scale*g
        g = scale = s = 0.0
        if i < nc-1:
            scale = sum(abs(a[i][k]) for k in range(l,nc))
            if scale:
                for k in range(l,nc):
                    a[i][k] /= scale
                    s += a[i][k]*a[i][k]
                f = a[i][l]
                g = -sign(math.sqrt(s),f)
                h = f*g-s
                a[i][l] = f-g
                for k in range(l,nc): rv[k] = a[i][k]/h
                for j in range(l,nr):
                    s = sum(a[j][k]*a[i][k] for k in range(l,nc))
                    for k in range(l,nc): a[j][k] += s*rv[k]
                for k in range(l,nc): a[i][k] *= scale
        anorm = max(anorm,abs(w[i])+abs(rv[i]))
    # Accumulate right Householder transformations.
    g = 0.0
    for i in range(nc-1,-1,-1):
        l = i+1
        if i < nc-1:
            if g:
                for j in range(l,nc): v[j][i] = (a[i][j]/a[i][l])/g
                for j in range(l,nc):
                    s = sum(a[i][k]*v[k][j] for k in range(l,nc))
                    for k in range(l,nc): v[k][j] += s*v[k][i]
            for j in range(l,nc): v[i][j] = v[j][i] = 0.0
        v[i][i] = 1.0
        g = rv[i]
    # Accumulate left Householder transformations.
    for i in range(nc-1,-1,-1):
        l = i+1
        g = w[i]
        for j in range(l,nc): a[i][j] = 0.0
        if g:
            inv = 1.0/g
            for j in range(l,nc):
                s = sum(a[k][i]*a[k][j] for k in range(l,nr))
                f = (s/a[i][i])*inv
                for k in range(i,nr): a[k][j] += f*a[k][i]
            for j in range(i,nr): a[j][i] *= inv
        else:
            for j in range(i,nr): a[j][i] = 0.0
        a[i][i] += 1.0
    # Diagonalize the bidiagonal matrix by implicit QR.
    eps = sys.float_info.epsilon
    for k in range(nc-1,-1,-1):
        for iteration in range(100):
            check()
            flag = True
            for l in range(k,-1,-1):
                nm = l-1
                if abs(rv[l]) <= eps*anorm:
                    flag = False
                    break
                if nm >= 0 and abs(w[nm]) <= eps*anorm: break
            if flag:
                c, s = 0.0, 1.0
                for i in range(l,k+1):
                    f = s*rv[i]
                    rv[i] = c*rv[i]
                    if abs(f) <= eps*anorm: break
                    g = w[i]
                    h = math.hypot(f,g)
                    w[i] = h
                    c, s = g/h, -f/h
                    for j in range(nr):
                        y,z = a[j][nm],a[j][i]
                        a[j][nm],a[j][i] = y*c+z*s,z*c-y*s
            z = w[k]
            if l == k:
                if z < 0.0:
                    w[k] = -z
                    for j in range(nc): v[j][k] = -v[j][k]
                break
            if iteration == 99: raise ArithmeticError('SVD QR did not converge')
            x = w[l]
            nm = k-1
            y,g,h = w[nm],rv[nm],rv[k]
            f = ((y-z)*(y+z)+(g-h)*(g+h))/(2.0*h*y)
            g = math.hypot(f,1.0)
            f = ((x-z)*(x+z)+h*((y/(f+sign(g,f)))-h))/x
            c = s = 1.0
            for j in range(l,nm+1):
                i = j+1
                g,y = rv[i],w[i]
                h = s*g
                g = c*g
                z = math.hypot(f,h)
                rv[j] = z
                c,s = f/z,h/z
                f,g = x*c+g*s,g*c-x*s
                h = y*s
                y *= c
                for jj in range(nc):
                    x,z = v[jj][j],v[jj][i]
                    v[jj][j],v[jj][i] = x*c+z*s,z*c-x*s
                z = math.hypot(f,h)
                w[j] = z
                if z: c,s = f/z,h/z
                f,x = c*g+s*y,c*y-s*g
                for jj in range(nr):
                    y,z = a[jj][j],a[jj][i]
                    a[jj][j],a[jj][i] = y*c+z*s,z*c-y*s
            rv[l],rv[k],w[k] = 0.0,f,x
    order = sorted(range(nc),key=lambda i:w[i],reverse=True)
    return ([[row[i] for i in order] for row in a],
            [w[i] for i in order],[[row[i] for i in order] for row in v])

def svd(a):
    check()
    if len(a) >= len(a[0]): return tall_svd(a)
    u,s,v = tall_svd(transpose(a))
    return v,s,u

def prefix(c,m):
    check()
    q = math.sqrt(2.0/m)
    return [[q*z for z in row[:m]] for row in c]
def project_y(u,y): return [dot(col,y) for col in zip(*u)]
def coefficients(v,s,uy,lam):
    check()
    if lam == 0.0:
        factors = [b/z if z > RHO*s[0] else 0.0 for z,b in zip(s,uy)]
    else:
        factors = [b*z/(z*z+N*lam) for z,b in zip(s,uy)]
    return [dot(row,factors) for row in v]
def residual(a,coef,y):
    return math.sqrt(sum((dot(row,coef)-t)**2 for row,t in zip(a,y))/dot(y,y))
def mse(a,coef,y):
    total = 0.0
    for i,(row,t) in enumerate(zip(a,y)):
        if i % 100 == 0: check()
        e = dot(row,coef)-t
        total += e*e
    val = total/len(y)
    if not math.isfinite(val): raise ArithmeticError('nonfinite MSE')
    return val

def repetition(seed):
    check()
    rng = random.Random(seed)
    x = [[rng.gauss(0.0,1.0) for _ in range(DIM)] for _ in range(N)]
    xt = [[rng.gauss(0.0,1.0) for _ in range(DIM)] for _ in range(NT)]
    tau = math.sqrt(0.2)
    y = [row[0]+rng.gauss(0.0,tau) for row in x]
    yt = [row[0]+rng.gauss(0.0,tau) for row in xt]
    # Draws occur after data/noise, from disjoint independent PRNG draws.
    omega = [[rng.gauss(0.0,0.5) for _ in range(DIM)] for _ in range(MAXM)]
    phase = [rng.uniform(0.0,2.0*math.pi) for _ in range(MAXM)]
    def cache(rows):
        out = []
        for i,row in enumerate(rows):
            if i % 50 == 0: check()
            out.append([math.cos(dot(row,w)+b) for w,b in zip(omega,phase)])
        return out
    c,ct = cache(x),cache(xt)
    scan = []
    threshold = None
    for m in range(1,181):
        a = prefix(c,m)
        u,s,v = svd(a)
        uy = project_y(u,y)
        coef = coefficients(v,s,uy,0.0)
        r = residual(a,coef,y)
        check()
        scan.append([m,r,sum(z > RHO*s[0] for z in s)])
        if r <= TOL:
            threshold = m
            break
    errors = {key:[] for key in KEYS}
    train_residuals = []
    singular_extremes = []
    for m in GRID:
        a,at = prefix(c,m),prefix(ct,m)
        u,s,v = svd(a)
        uy = project_y(u,y)
        singular_extremes.append([s[0],s[-1]])
        for lam,key in zip(LAMBDAS,KEYS):
            coef = coefficients(v,s,uy,lam)
            if lam == 0.0: train_residuals.append(residual(a,coef,y))
            errors[key].append(mse(at,coef,yt))
    check()
    return {'seed':seed,'threshold':threshold if threshold is not None else 'not reached',
            'threshold_censored':threshold is None,'threshold_scan_m_residual_rank':scan,
            'test_mse':errors,'baseline_relative_train_residual':train_residuals,
            'curve_singular_max_min':singular_extremes,
            'work_counts':{'threshold_svd_calls':len(scan),'curve_svd_calls':12,'curve_coefficient_fits':48}}

completed = []
stopped = False
failure = None
for seed in SEEDS:
    try:
        check()
        record = repetition(seed)
        completed.append(record)
    except Deadline:
        stopped = True
        break
    except (ArithmeticError,OverflowError,ZeroDivisionError) as exc:
        failure = {'seed':seed,'error':str(exc)}
        break
S = len(completed)
averages = {k:[sum(r['test_mse'][k][j] for r in completed)/S for j in range(len(GRID))] for k in KEYS} if S else {}
threshold_median = statistics.median([r['threshold'] for r in completed]) if S and all(not r['threshold_censored'] for r in completed) else None
window = [m for m in GRID if abs(m/threshold_median-1.0) <= 0.1+1e-14] if threshold_median else []
left = max((m for m in GRID if window and m < min(window)),default=None)
right = min((m for m in GRID if window and m > max(window)),default=None)
valid = bool(window and left is not None and right is not None)
summary = {'available':valid,'median_interpolation_threshold':threshold_median,
           'window':window,'left_flank':left,'right_flank':right,
           'peak_mse':None,'unregularized_peak_prominence':None,'ridge_peak_reduction':None}
if valid:
    idx = [GRID.index(m) for m in window]
    peaks = {k:max(averages[k][j] for j in idx) for k in KEYS}
    prominence = peaks['0']-max(averages['0'][GRID.index(left)],averages['0'][GRID.index(right)])
    summary.update(peak_mse=peaks,unregularized_peak_prominence=prominence,
                   ridge_peak_reduction={k:peaks['0']-peaks[k] for k in KEYS[1:]})
else:
    summary['unavailable_reason'] = 'No completed repetitions, at least one threshold not reached, empty window, or missing flank.'
# Primary paired comparisons at every grid count. All arrays share completed-seed order.
# Lower treatment MSE supports peak reduction; these raw comparisons are not
# substituted for max-of-averaged-curve window statistics.
control,treatment = {},{}
for k in KEYS[1:]:
    for j,m in enumerate(GRID):
        regime = 'lambda='+k+',m='+str(m)
        control[regime] = [r['test_mse']['0'][j] for r in completed]
        treatment[regime] = [r['test_mse'][k][j] for r in completed]
paired_peaks = None
if valid:
    paired_peaks = {'description':'Per-repetition maxima in the common median-threshold window; distinct from maxima of averaged curves.',
                    'seeds':[r['seed'] for r in completed],
                    'control':{k:[max(r['test_mse']['0'][j] for j in idx) for r in completed] for k in KEYS[1:]},
                    'treatment':{k:[max(r['test_mse'][k][j] for j in idx) for r in completed] for k in KEYS[1:]},
                    'higher_supports':False,'control_censored':[False]*S,'treatment_censored':[False]*S}
result = {
    'experiment':'Fourier-feature interpolation peak and ridge comparison',
    'method':'Gaussian-spectrum cosine features; direct Golub-Reinsch thin SVD; cutoff minimum-norm least squares and untruncated SVD ridge',
    'source_scope':'The supplied paper example uses ReLU and feature-dependent optimal ridge; this is the explicitly designed Fourier experiment with fixed ridge strengths, not a replication of that example.',
    'settings':{'n_train':N,'n_test':NT,'d':DIM,'input_distribution':'standard multivariate Gaussian',
                'target':'first input coordinate','noise_variance':0.2,'kernel_bandwidth':2.0,
                'phase_distribution':'uniform on [0, 2pi)','feature_sequence':'nested prefixes within each repetition',
                'test_labels':'independent noisy labels','linear_algebra_threads':1,'arithmetic':'float64',
                'svd_relative_cutoff':RHO,'interpolation_residual_tolerance':TOL,'near_threshold_fraction':0.1,
                'feature_counts':GRID,'ridge_parameters':LAMBDAS[1:],'baseline_regularization':0,
                'ridge_objective':'||Phi a-y||^2/n_train + lambda ||a||^2',
                'threshold_scan':{'first_feature_count':1,'last_feature_count':180,'increment':1},
                'work_cap':{'wall_clock_seconds':280,'maximum_features_per_repetition':180,
                            'maximum_threshold_svd_calls_per_repetition':180,'maximum_curve_svd_calls_per_repetition':12,
                            'maximum_coefficient_fits_per_repetition':48},
                'rng':'stdlib random.Random, Gaussian draws via gauss; one independent stream per seed'},
    'requested_seeds':SEEDS,'completed_seeds':[r['seed'] for r in completed],
    'completed_repetition_count':S,'discarded_or_unstarted_seeds':SEEDS[S:],
    'deadline_reached':stopped,'numerical_failure':failure,
    'elapsed_seconds':time.monotonic()-START,'repetitions':completed,
    'average_test_mse':averages,'mse_units':'squared-label units',
    'window_metrics':summary,
    'paired_comparison':{'metric':'Test MSE by lambda and feature count','seeds':[r['seed'] for r in completed],
                         'control':control,'treatment':treatment,'higher_supports':False,
                         'control_censored':{k:[False]*S for k in control},
                         'treatment_censored':{k:[False]*S for k in treatment}},
    'paired_window_peaks':paired_peaks,
    'interpretation':'C0 > 0 supports the near-threshold peak criterion; D_lambda > 0 supports reduction for that fixed lambda. A finite setting is not a universal result.'}
encoded = json.dumps(result,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
assert len(encoded) <= 100000
with open('result.json','wb') as f: f.write(encoded)
