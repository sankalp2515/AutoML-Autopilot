"""Robust CSV ingestion — normalize any real-world CSV to a clean, comma-delimited
UTF-8 file at a SINGLE chokepoint, so every downstream sandbox template (which all
hardcode `pd.read_csv(dataset_path)`) works unchanged.

Motivating failure (bank+marketing, UCI 222): the file is SEMICOLON-delimited and
quoted. Read with the default comma delimiter, pandas returns ONE giant column and
the target `y` vanishes — which cascaded into "Target column __target__ not found"
and an astype crash. A large fraction of real datasets (European exports, TSV,
pipe-delimited, latin-1 encoded) hit the same wall.

Doctrine: fix it ONCE, at ingestion, by rewriting the file — not by threading a
delimiter parameter through dozens of templates. Idempotent: an already-clean file
is left byte-identical (no rewrite), so re-runs and retries are safe.
"""
from __future__ import annotations

import csv
import io
import os

from app.core.logging import get_logger

_log = get_logger("ingest")

# Delimiters we detect, in priority order for the tie-break heuristic.
_CANDIDATES = [",", ";", "\t", "|"]
_ENCODINGS = ["utf-8-sig", "utf-8", "latin-1", "cp1252"]


def _read_text(path: str) -> tuple[str, str]:
    """Return (text, encoding) trying a sequence of common encodings."""
    raw = open(path, "rb").read()
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc), ("utf-8" if enc == "utf-8-sig" else enc)
        except UnicodeDecodeError:
            continue
    # Last resort: never fail ingestion over an odd byte.
    return raw.decode("latin-1", errors="replace"), "latin-1"


def _sniff_delimiter(sample: str) -> str:
    """Detect the delimiter. csv.Sniffer first; fall back to a header-consistency
    heuristic (the delimiter that yields the most, and a stable count of, fields)."""
    header = sample.splitlines()[0] if sample.splitlines() else ""
    # Fast path: Sniffer on a bounded sample.
    try:
        dialect = csv.Sniffer().sniff(sample[:8192], delimiters="".join(_CANDIDATES))
        if dialect.delimiter in _CANDIDATES:
            return dialect.delimiter
    except csv.Error:
        pass
    # Heuristic: pick the candidate that splits the HEADER into the most columns;
    # break ties by counting header occurrences (comma preferred last so a genuine
    # comma file isn't overridden by commas inside quoted ; fields).
    best, best_cols = ",", 1
    for d in _CANDIDATES:
        try:
            n = len(next(csv.reader(io.StringIO(header), delimiter=d)))
        except csv.Error:
            n = header.count(d) + 1
        if n > best_cols:
            best, best_cols = d, n
    return best


def normalize_csv(path: str) -> dict:
    """Ensure `path` is a clean comma-delimited UTF-8 CSV, rewriting IN PLACE only
    if needed. Returns a small report. Never raises on a readable file."""
    try:
        text, encoding = _read_text(path)
    except OSError as e:
        return {"ok": False, "reason": f"unreadable: {e}"}

    if not text.strip():
        return {"ok": False, "reason": "empty file"}

    delimiter = _sniff_delimiter(text)
    already_clean = (delimiter == "," and encoding == "utf-8")
    if already_clean:
        return {"ok": True, "changed": False, "delimiter": ",", "encoding": "utf-8"}

    # Re-parse with the detected delimiter and rewrite as standard comma/UTF-8.
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        rows = list(reader)
        n_cols = len(rows[0]) if rows else 0
        tmp = path + ".norm"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            csv.writer(fh).writerows(rows)
        os.replace(tmp, path)
        _log.info("csv_normalized", path=os.path.basename(path),
                  delimiter=repr(delimiter), encoding=encoding, n_cols=n_cols)
        return {"ok": True, "changed": True, "delimiter": delimiter,
                "encoding": encoding, "n_cols": n_cols}
    except Exception as e:  # rewrite failed → leave the original untouched
        _log.warning("csv_normalize_failed", path=os.path.basename(path), error=str(e)[:200])
        return {"ok": False, "reason": f"rewrite failed: {e}"}
