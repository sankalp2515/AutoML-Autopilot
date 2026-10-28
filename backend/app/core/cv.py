"""Purged & embargoed cross-validation for financial time series (Tower track).

Standard K-fold leaks on financial data two ways: (1) when a label spans several
bars, training samples whose label window overlaps the test window leak future
information — they must be *purged*; (2) serial correlation right after the test
window inflates scores — those bars must be *embargoed*. This is the López de
Prado purged/embargoed K-fold. Test folds are contiguous and time-ordered (never
shuffled), so the future never trains the past.

Pure-numpy and framework-agnostic: yields (train_idx, test_idx) like any sklearn
splitter, so it drops into the existing CV call sites.
"""

from __future__ import annotations

import numpy as np


def purged_kfold_indices(n_samples: int, n_splits: int = 5,
                         embargo_pct: float = 0.01, purge_gap: int = 0):
    """Yield (train_idx, test_idx) for purged + embargoed K-fold.

    - Test folds are contiguous, time-ordered blocks covering all samples.
    - Training samples within `purge_gap` bars of either side of the test block are
      purged (label-overlap leakage).
    - `embargo_pct` of samples immediately AFTER the test block are also dropped
      from training (serial-correlation leakage).
    """
    if n_splits < 2:
        raise ValueError("n_splits must be >= 2")
    if n_splits > n_samples:
        raise ValueError("n_splits cannot exceed n_samples")

    indices = np.arange(n_samples)
    embargo = int(n_samples * embargo_pct)

    for test_idx in np.array_split(indices, n_splits):
        if test_idx.size == 0:
            continue
        t0, t1 = int(test_idx[0]), int(test_idx[-1])
        train_mask = np.ones(n_samples, dtype=bool)
        # Purge a window around the test block, and embargo immediately after it.
        lo = max(0, t0 - purge_gap)
        hi = min(n_samples, t1 + 1 + purge_gap + embargo)
        train_mask[lo:hi] = False
        train_idx = indices[train_mask]
        yield train_idx, test_idx


class PurgedKFold:
    """sklearn-style splitter wrapper so it plugs into cross_val_score / loops."""

    def __init__(self, n_splits: int = 5, embargo_pct: float = 0.01, purge_gap: int = 0):
        self.n_splits = n_splits
        self.embargo_pct = embargo_pct
        self.purge_gap = purge_gap

    def get_n_splits(self, X=None, y=None, groups=None) -> int:
        return self.n_splits

    def split(self, X, y=None, groups=None):
        n = len(X)
        yield from purged_kfold_indices(n, self.n_splits, self.embargo_pct, self.purge_gap)
