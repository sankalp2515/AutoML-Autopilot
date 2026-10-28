"""Quant-aware backtest metrics (Tower track — #5 Reproducible Research Toolkit).

Turns a model's predictions on a time-ordered series into trading-style performance
metrics: a transaction-cost-aware equity curve, Sharpe/Sortino, max drawdown, and
turnover. Pure-numpy so it runs in the backend AND is unit-testable; the time-series
evaluator can call it to report Sharpe/drawdown alongside RMSE.

Convention: `actual_returns[t]` is the realized return of the asset over period t.
`signal[t]` is the position taken INTO period t (long>0 / flat 0 / short<0), and MUST
be derived from information available BEFORE t (no look-ahead). Strategy return for the
period is `position * actual_return - transaction_cost(position change)`.
"""

from __future__ import annotations

import numpy as np

_EPS = 1e-12


def positions_from_predictions(pred_returns: np.ndarray, mode: str = "sign",
                               long_only: bool = False) -> np.ndarray:
    """Map predicted returns to positions in [-1, 1]. 'sign' = full long/short on the
    predicted direction; 'proportional' = clipped to [-1, 1]. Shifted by one period by
    the caller (or here) so a prediction acts on the NEXT bar — never the current one."""
    p = np.asarray(pred_returns, dtype=float)
    if mode == "proportional":
        pos = np.clip(p, -1.0, 1.0)
    else:
        pos = np.sign(p)
    if long_only:
        pos = np.clip(pos, 0.0, 1.0)
    return pos


def strategy_returns(actual_returns: np.ndarray, signal: np.ndarray,
                     cost_bps: float = 1.0) -> np.ndarray:
    """Per-period net strategy returns. `cost_bps` is charged on the CHANGE in position
    (round-trip turnover), in basis points (1 bp = 0.01%)."""
    r = np.asarray(actual_returns, dtype=float)
    pos = np.asarray(signal, dtype=float)
    n = min(len(r), len(pos))
    r, pos = r[:n], pos[:n]
    gross = pos * r
    pos_change = np.abs(np.diff(pos, prepend=0.0))
    cost = pos_change * (cost_bps / 1e4)
    return gross - cost


def equity_curve(returns: np.ndarray, starting: float = 1.0) -> np.ndarray:
    """Cumulative compounded equity from per-period returns."""
    r = np.asarray(returns, dtype=float)
    return starting * np.cumprod(1.0 + r)


def sharpe_ratio(returns: np.ndarray, periods_per_year: int = 252,
                 risk_free: float = 0.0) -> float:
    r = np.asarray(returns, dtype=float)
    if r.size < 2:
        return 0.0
    excess = r - risk_free / periods_per_year
    sd = excess.std(ddof=1)
    if sd < _EPS:
        return 0.0
    return float(np.sqrt(periods_per_year) * excess.mean() / sd)


def sortino_ratio(returns: np.ndarray, periods_per_year: int = 252,
                  risk_free: float = 0.0) -> float:
    r = np.asarray(returns, dtype=float)
    if r.size < 2:
        return 0.0
    excess = r - risk_free / periods_per_year
    downside = excess[excess < 0]
    dd = np.sqrt((downside ** 2).mean()) if downside.size else 0.0
    if dd < _EPS:
        return 0.0
    return float(np.sqrt(periods_per_year) * excess.mean() / dd)


def max_drawdown(equity: np.ndarray) -> float:
    """Largest peak-to-trough decline of an equity curve, as a negative fraction."""
    e = np.asarray(equity, dtype=float)
    if e.size == 0:
        return 0.0
    running_max = np.maximum.accumulate(e)
    drawdown = (e - running_max) / running_max
    return float(drawdown.min())


def turnover(signal: np.ndarray) -> float:
    """Average per-period absolute change in position (trading intensity)."""
    pos = np.asarray(signal, dtype=float)
    if pos.size == 0:
        return 0.0
    return float(np.abs(np.diff(pos, prepend=0.0)).mean())


def backtest_summary(actual_returns, pred_returns, *, cost_bps: float = 1.0,
                     periods_per_year: int = 252, mode: str = "sign",
                     long_only: bool = False) -> dict:
    """End-to-end: predictions → positions → net returns → performance metrics.

    A directional-accuracy and a buy-&-hold benchmark are included so the strategy is
    judged against a naive baseline, mirroring the pipeline's 'beat the baseline' rule.
    """
    actual = np.asarray(actual_returns, dtype=float)
    pos = positions_from_predictions(pred_returns, mode=mode, long_only=long_only)
    net = strategy_returns(actual, pos, cost_bps=cost_bps)
    eq = equity_curve(net)

    bh_eq = equity_curve(actual)  # buy-and-hold benchmark
    hit_rate = float((np.sign(pred_returns[:len(actual)]) == np.sign(actual)).mean()) if actual.size else 0.0

    return {
        "sharpe": round(sharpe_ratio(net, periods_per_year), 4),
        "sortino": round(sortino_ratio(net, periods_per_year), 4),
        "max_drawdown": round(max_drawdown(eq), 4),
        "turnover": round(turnover(pos), 4),
        "total_return": round(float(eq[-1] - 1.0) if eq.size else 0.0, 4),
        "buy_hold_return": round(float(bh_eq[-1] - 1.0) if bh_eq.size else 0.0, 4),
        "hit_rate": round(hit_rate, 4),
        "n_periods": int(actual.size),
        "cost_bps": cost_bps,
        "equity_curve": [round(float(x), 6) for x in eq],
    }
