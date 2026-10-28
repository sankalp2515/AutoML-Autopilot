"""parallel_map — Ray-or-sequential fan-out (Tower #1)."""

import pytest

from app.config import settings
from app.core.parallel import parallel_map


def _square(x):
    return x * x


def test_sequential_preserves_order_and_values(monkeypatch):
    monkeypatch.setattr(settings, "USE_RAY", False)
    assert parallel_map(_square, [1, 2, 3, 4]) == [1, 4, 9, 16]


def test_use_ray_false_never_imports_ray(monkeypatch):
    monkeypatch.setattr(settings, "USE_RAY", False)
    # Explicit override also forces sequential.
    assert parallel_map(_square, [5, 6], use_ray=False) == [25, 36]


def test_falls_back_to_sequential_when_ray_unavailable(monkeypatch):
    # Forcing use_ray=True with Ray absent must NOT raise — it degrades gracefully.
    monkeypatch.setattr(settings, "USE_RAY", True)
    assert parallel_map(_square, [2, 3]) == [4, 9]


def test_ray_backend_when_installed(monkeypatch):
    ray = pytest.importorskip("ray")
    monkeypatch.setattr(settings, "USE_RAY", True)
    try:
        out = parallel_map(_square, [1, 2, 3])
    finally:
        if ray.is_initialized():
            ray.shutdown()
    assert out == [1, 4, 9]
