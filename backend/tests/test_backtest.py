"""Quant backtest metrics (Tower track) — verified against hand-computable cases."""

import numpy as np
import pytest

from app.core import backtest as bt


def test_equity_curve_compounds():
    eq = bt.equity_curve([0.1, 0.1])
    assert eq[-1] == pytest.approx(1.21)


def test_max_drawdown_simple():
    # peak 1.2 → trough 0.6 = -50%
    eq = np.array([1.0, 1.2, 0.6, 0.9])
    assert bt.max_drawdown(eq) == pytest.approx(-0.5)


def test_sharpe_zero_when_flat():
    assert bt.sharpe_ratio([0.0, 0.0, 0.0]) == 0.0


def test_sharpe_positive_for_steady_gains():
    assert bt.sharpe_ratio([0.01, 0.012, 0.009, 0.011], periods_per_year=252) > 0


def test_positions_sign_and_long_only():
    pos = bt.positions_from_predictions([0.5, -0.3, 0.0])
    assert list(pos) == [1.0, -1.0, 0.0]
    lo = bt.positions_from_predictions([0.5, -0.3], long_only=True)
    assert list(lo) == [1.0, 0.0]


def test_transaction_costs_reduce_returns():
    actual = [0.02, 0.02]
    signal = [1.0, -1.0]            # flips position → incurs turnover cost
    free = bt.strategy_returns(actual, signal, cost_bps=0.0)
    charged = bt.strategy_returns(actual, signal, cost_bps=50.0)
    assert charged.sum() < free.sum()


def test_perfect_foresight_beats_buy_and_hold():
    rng = np.random.default_rng(0)
    actual = rng.normal(0, 0.01, 200)
    summary = bt.backtest_summary(actual, actual, cost_bps=0.0)  # predict = truth
    assert summary["hit_rate"] == 1.0
    assert summary["total_return"] >= summary["buy_hold_return"]
    assert summary["sharpe"] > 0


def test_summary_has_all_keys():
    s = bt.backtest_summary([0.01, -0.02, 0.03], [0.5, -0.5, 0.5])
    for k in ("sharpe", "sortino", "max_drawdown", "turnover", "total_return",
              "buy_hold_return", "hit_rate", "n_periods", "equity_curve"):
        assert k in s


# ── TS evaluator wiring (trading_diagnostic) ──────────────────────────────────
def test_trading_diagnostic_on_price_series():
    from app.agents.ts_agents import trading_diagnostic
    # A clean uptrend the forecast tracks → positive Sharpe, a real summary dict.
    actual = [100, 101, 102, 103, 104, 105, 106]
    pred = [101, 102, 103, 104, 105, 106, 107]   # always forecasts up
    out = trading_diagnostic(actual, pred, cost_bps=1.0)
    assert out is not None
    assert "sharpe" in out and "max_drawdown" in out
    assert out["hit_rate"] == 1.0                # direction always correct on an uptrend


def test_trading_diagnostic_too_short_returns_none():
    from app.agents.ts_agents import trading_diagnostic
    assert trading_diagnostic([100, 101], [101, 102]) is None
