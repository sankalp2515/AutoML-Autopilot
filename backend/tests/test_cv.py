"""Purged/embargoed K-fold (quant CV) — correctness guarantees."""

import numpy as np
import pytest

from app.core.cv import PurgedKFold, purged_kfold_indices


def test_train_and_test_never_overlap():
    for train, test in purged_kfold_indices(100, n_splits=5):
        assert set(train).isdisjoint(set(test))


def test_test_folds_cover_all_samples_once():
    seen = []
    for _, test in purged_kfold_indices(100, n_splits=5):
        seen.extend(test.tolist())
    assert sorted(seen) == list(range(100))     # every index tested exactly once


def test_test_folds_are_contiguous_and_time_ordered():
    # Each test block is a contiguous, increasing range (never shuffled).
    for _, test in purged_kfold_indices(100, n_splits=5):
        assert list(test) == list(range(test[0], test[-1] + 1))


def test_embargo_removes_bars_after_test_block():
    # With a large embargo, training must exclude the window right after each test fold.
    folds = list(purged_kfold_indices(100, n_splits=5, embargo_pct=0.05))
    train, test = folds[0]                       # first fold: test is the earliest block
    t1 = int(test[-1])
    embargo = int(100 * 0.05)
    forbidden = set(range(t1 + 1, t1 + 1 + embargo))
    assert forbidden.isdisjoint(set(train))


def test_purge_gap_widens_the_excluded_window():
    no_gap = set(next(iter(purged_kfold_indices(100, 5, embargo_pct=0.0, purge_gap=0)))[0])
    gapped = set(next(iter(purged_kfold_indices(100, 5, embargo_pct=0.0, purge_gap=3)))[0])
    assert len(gapped) < len(no_gap)             # more samples purged with a gap


def test_sklearn_style_wrapper():
    X = np.zeros((50, 3))
    cv = PurgedKFold(n_splits=5, embargo_pct=0.02)
    assert cv.get_n_splits() == 5
    splits = list(cv.split(X))
    assert len(splits) == 5
    for train, test in splits:
        assert set(train).isdisjoint(set(test))


def test_invalid_n_splits():
    with pytest.raises(ValueError):
        list(purged_kfold_indices(100, n_splits=1))
    with pytest.raises(ValueError):
        list(purged_kfold_indices(3, n_splits=5))
