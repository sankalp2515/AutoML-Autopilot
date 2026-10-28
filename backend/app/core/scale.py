"""Scale strategy — the system-design seam for datasets from 150 rows to billions.

One function decides HOW a run trains, so every future scale lever plugs in at
a single point instead of per-agent heuristics:

  strategy = "in_memory"  — dataset fits comfortably; today's default path.
  strategy = "sampled"    — capped at the splitter (MAX_TRAIN_ROWS) with the
                            S1 learning-curve proof / escalation.
  strategy = "streaming"  — rows-that-matter exceed even the escalated cap:
                            the model menu prefers out-of-core trainers
                            (partial_fit learners over chunked reads; XGBoost
                            external-memory is the planned upgrade) so ALL rows
                            are learned from in bounded RAM.
  strategy = "distributed"— (S3, forward-declared) a Ray cluster is attached
                            (RAY_ADDRESS): candidate CV fans out across workers
                            and streaming shards partition by worker. Today it
                            reports as available-but-unwired so operators can
                            see the seam; execution still runs single-node.

Doctrine: the strategy is a MECHANIC (code decides from measured size), not an
ML decision — but it is REPORTED in the decision log so the run explains itself.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class ScalePlan:
    strategy: str            # in_memory | sampled | streaming
    n_rows: int
    reason: str
    ray_available: bool = False


# Default: above this many training rows even the escalated cap is a real
# information-loss risk — prefer out-of-core learners that see every row. The
# effective value is read from settings (STREAMING_THRESHOLD_ROWS), so it can be
# lowered to exercise the streaming path on a smaller real dataset.
STREAMING_THRESHOLD_ROWS = 2_000_000


def _streaming_threshold() -> int:
    return int(getattr(settings, "STREAMING_THRESHOLD_ROWS", STREAMING_THRESHOLD_ROWS)
               or STREAMING_THRESHOLD_ROWS)


def count_rows_fast(csv_path: str, sample_bytes: int = 4_000_000) -> int:
    """Estimate row count from average line length over a head sample — O(MB),
    not O(file). Exact enough to pick a strategy (never used for splitting)."""
    try:
        size = os.path.getsize(csv_path)
        with open(csv_path, "rb") as fh:
            head = fh.read(min(sample_bytes, size))
        lines = head.count(b"\n") or 1
        avg = len(head) / lines
        return max(0, int(size / max(avg, 1)) - 1)
    except OSError:
        return 0


def plan_for(csv_path: str) -> ScalePlan:
    n = count_rows_fast(csv_path)
    cap = int(getattr(settings, "MAX_TRAIN_ROWS", 0) or 0)
    hard = int(getattr(settings, "MAX_TRAIN_ROWS_HARD", 0) or 0)
    ray = bool(getattr(settings, "RAY_ADDRESS", "") or "")

    threshold = _streaming_threshold()
    if n > threshold:
        return ScalePlan(
            "streaming", n,
            f"~{n:,} rows exceed the streaming threshold ({threshold:,}) — "
            "out-of-core learners (partial_fit over chunks) are preferred so every row is "
            "learned from in bounded memory; the holdout stays a full honest slice.",
            ray_available=ray,
        )
    if cap and n > cap:
        return ScalePlan(
            "sampled", n,
            f"~{n:,} rows > MAX_TRAIN_ROWS={cap:,} — stratified sample with a learning-curve "
            f"proof (escalates to {hard:,} if the curve still climbs).",
            ray_available=ray,
        )
    return ScalePlan("in_memory", n, f"~{n:,} rows fit in memory — full-data training.",
                     ray_available=ray)
