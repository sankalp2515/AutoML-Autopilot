"""Robust CSV ingestion — delimiter + encoding normalization.

Regression for the bank+marketing (UCI 222) failure: a ;-delimited file read as
comma gives ONE column and the target vanishes.
"""

import pandas as pd
import pytest

from app.core import ingest


def _write(tmp_path, name, content, encoding="utf-8"):
    p = tmp_path / name
    p.write_text(content, encoding=encoding)
    return str(p)


def test_semicolon_is_normalized_to_comma(tmp_path):
    # The exact shape of bank-full.csv: quoted, semicolon-delimited.
    path = _write(tmp_path, "bank.csv",
                  '"age";"job";"y"\n58;"management";"no"\n44;"technician";"yes"\n')
    rep = ingest.normalize_csv(path)
    assert rep["ok"] and rep["changed"] and rep["delimiter"] == ";"
    df = pd.read_csv(path)                      # default comma — must now work
    assert list(df.columns) == ["age", "job", "y"]
    assert set(df["y"]) == {"no", "yes"}
    assert len(df) == 2


def test_tab_delimited_normalized(tmp_path):
    path = _write(tmp_path, "t.tsv", "a\tb\tc\n1\t2\t3\n4\t5\t6\n")
    rep = ingest.normalize_csv(path)
    assert rep["delimiter"] == "\t" and rep["changed"]
    assert list(pd.read_csv(path).columns) == ["a", "b", "c"]


def test_pipe_delimited_normalized(tmp_path):
    path = _write(tmp_path, "p.csv", "x|y|z\n1|2|3\n")
    rep = ingest.normalize_csv(path)
    assert rep["delimiter"] == "|"


def test_clean_comma_file_is_untouched(tmp_path):
    path = _write(tmp_path, "clean.csv", "a,b,c\n1,2,3\n4,5,6\n")
    before = open(path, "rb").read()
    rep = ingest.normalize_csv(path)
    assert rep["ok"] and rep["changed"] is False
    assert open(path, "rb").read() == before   # byte-identical: idempotent


def test_latin1_encoding_normalized(tmp_path):
    # 'café' in latin-1 with a comma delimiter → re-encoded to utf-8.
    path = _write(tmp_path, "fr.csv", "name,city\nJosé,Café\n", encoding="latin-1")
    rep = ingest.normalize_csv(path)
    assert rep["ok"]
    df = pd.read_csv(path, encoding="utf-8")
    assert df.iloc[0]["city"] == "Café"


def test_semicolon_with_commas_inside_quotes(tmp_path):
    # A ; file whose quoted fields CONTAIN commas — the sniffer must not pick ",".
    path = _write(tmp_path, "tricky.csv",
                  'id;note;y\n1;"a, b, c";no\n2;"d, e";yes\n')
    rep = ingest.normalize_csv(path)
    assert rep["delimiter"] == ";"
    df = pd.read_csv(path)
    assert list(df.columns) == ["id", "note", "y"]
    assert df.iloc[0]["note"] == "a, b, c"     # comma preserved inside the field


def test_empty_file_reported(tmp_path):
    path = _write(tmp_path, "empty.csv", "")
    assert ingest.normalize_csv(path)["ok"] is False
