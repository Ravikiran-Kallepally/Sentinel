# ADR-003: scikit-learn HistGradientBoosting instead of LightGBM for the fraud model

**Status:** Accepted

## Context

LightGBM (or XGBoost) with TreeSHAP is the usual production choice. During development, on Windows, fitting a
scikit-learn logistic regression with the default lbfgs solver (SciPy native code) and then constructing a LightGBM
dataset crashed the process with an access violation. Whether it crashed depended on import order (it passed when
PyTorch happened to load first) - a native OpenMP/BLAS runtime conflict, i.e. non-deterministic.

## Decision

Use `HistGradientBoostingClassifier` (the same histogram-based algorithm as LightGBM, no separate native
runtime) and perturbation-based attributions for per-transaction explanations. Use non-lbfgs solvers for logistic
regressions. Record the incident here and in code comments.

## Consequences

+ Reliable on every platform the learners use; same modelling concepts.
- No exact TreeSHAP; attributions are approximate (one extra prediction per feature).
- Revisit when the dependency stack is pinned in a container where LightGBM is known to be stable (next-steps #13).
Lesson: native-library conflicts are a real production risk - pin versions and run the full pipeline in CI.
