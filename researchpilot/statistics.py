from __future__ import annotations

import math
import random
from collections.abc import Sequence


def summarize(values: Sequence[float], confidence: float = 0.95) -> dict[str, float | int]:
    if not values:
        raise ValueError("at least one observation is required")
    xs = [float(v) for v in values]
    n = len(xs)
    mean = sum(xs) / n
    variance = sum((x - mean) ** 2 for x in xs) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(variance)
    # Normal approximation is clearly labeled; bootstrap is available below.
    z = 1.959963984540054 if confidence == 0.95 else 1.0
    margin = z * sd / math.sqrt(n)
    ordered = sorted(xs)
    mid = n // 2
    median = ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return {"n": n, "confidence_level": confidence, "mean": mean, "median": median, "variance": variance,
            "std_dev": sd, "ci_low": mean - margin, "ci_high": mean + margin}


def bootstrap_mean_ci(values: Sequence[float], samples: int = 2000, seed: int = 0,
                      alpha: float = 0.05) -> tuple[float, float]:
    if len(values) < 2:
        raise ValueError("bootstrap requires at least two observations")
    rng = random.Random(seed)
    xs = list(map(float, values))
    means = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(samples))
    lo = means[int(samples * alpha / 2)]
    hi = means[min(samples - 1, int(samples * (1 - alpha / 2)))]
    return lo, hi


def cohens_d(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) < 2 or len(b) < 2:
        raise ValueError("effect size requires two observations per group")
    sa, sb = summarize(a), summarize(b)
    pooled = math.sqrt(((len(a) - 1) * float(sa["variance"]) + (len(b) - 1) * float(sb["variance"])) / (len(a) + len(b) - 2))
    return (float(sa["mean"]) - float(sb["mean"])) / pooled if pooled else 0.0
