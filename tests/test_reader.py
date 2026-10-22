"""Leitura do jsonlog: rotação, compressão, linhas malformadas/truncadas e campos ausentes."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from sacla.reader import ReadReport, list_log_files, parse_timestamp, read_records

PATTERNS = ("*.json", "*.json.gz")


def _line(ts: str, **fields: object) -> str:
    return json.dumps({"timestamp": ts, "message": "m", **fields}) + "\n"


def test_files_are_read_in_name_order_across_compression(tmp_path: Path) -> None:
    (tmp_path / "postgresql-20260302.json").write_text(
        _line("2026-03-02 10:00:00.000 UTC", user="a"), encoding="utf-8"
    )
    with gzip.open(tmp_path / "postgresql-20260303.json.gz", "wt", encoding="utf-8") as fh:
        fh.write(_line("2026-03-03 10:00:00.000 UTC", user="b"))
    (tmp_path / "outro.txt").write_text("ignorado", encoding="utf-8")

    files = list_log_files(tmp_path, PATTERNS)
    users = [r.user for r in read_records(files)]

    assert [f.name for f in files] == ["postgresql-20260302.json", "postgresql-20260303.json.gz"]
    assert users == ["a", "b"]


def test_malformed_and_truncated_lines_are_counted_and_skipped(tmp_path: Path) -> None:
    content = (
        _line("2026-03-02 10:00:00.000 UTC", user="ok1")
        + "isto não é json\n"
        + json.dumps({"message": "sem timestamp"})
        + "\n"
        + _line("2026-03-02 10:00:01.000 UTC", user="ok2")
        + '{"timestamp": "2026-03-02 10:00:02.000 UTC", "mess'  # gravação interrompida
    )
    path = tmp_path / "postgresql-1.json"
    path.write_text(content, encoding="utf-8")
    report = ReadReport()

    users = [r.user for r in read_records([path], report)]

    assert users == ["ok1", "ok2"]
    assert (report.records, report.malformed, report.truncated) == (2, 2, 1)


def test_complete_last_line_without_newline_is_kept(tmp_path: Path) -> None:
    path = tmp_path / "postgresql-1.json"
    path.write_text(_line("2026-03-02 10:00:00.000 UTC", user="x").rstrip("\n"), encoding="utf-8")
    assert [r.user for r in read_records([path])] == ["x"]


def test_missing_and_mistyped_fields_become_none_and_unknown_fields_are_ignored(
    tmp_path: Path,
) -> None:
    path = tmp_path / "postgresql-1.json"
    path.write_text(
        _line("2026-03-02 10:00:00.000 UTC", pid="não-é-número", campo_novo=123), encoding="utf-8"
    )
    record = next(read_records([path]))
    assert record.user is None and record.pid is None and record.message == "m"


@pytest.mark.parametrize(
    ("value", "expected_utc"),
    [
        ("2026-03-02 11:00:00.250 UTC", "2026-03-02 11:00:00.250 UTC"),
        ("2026-03-02 08:00:00.250 -03", "2026-03-02 11:00:00.250 UTC"),
        ("2026-03-02 13:30:00.250 +0230", "2026-03-02 11:00:00.250 UTC"),
    ],
)
def test_timestamp_with_utc_or_numeric_offset(value: str, expected_utc: str) -> None:
    assert parse_timestamp(value) == parse_timestamp(expected_utc)


def test_unknown_zone_abbreviation_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_timestamp("2026-03-02 08:00:00.000 BRT")


def test_zone_abbreviations_come_from_config_and_unknown_ones_are_counted(tmp_path: Path) -> None:
    path = tmp_path / "postgresql-1.json"
    path.write_text(
        _line("2026-03-02 08:00:00.000 BRT", user="mapeado")
        + _line("2026-03-02 08:00:00.000 CET", user="desconhecido"),
        encoding="utf-8",
    )
    report = ReadReport()
    records = list(read_records([path], report, {"UTC": 0, "BRT": -180}))
    assert [r.user for r in records] == ["mapeado"]
    assert records[0].ts_ms == parse_timestamp("2026-03-02 11:00:00.000 UTC")
    assert (report.unknown_zone, report.malformed) == (1, 0)


def test_cut_gzip_file_keeps_previous_lines_and_counts_truncation(tmp_path: Path) -> None:
    whole = tmp_path / "inteiro.json.gz"
    with gzip.open(whole, "wt", encoding="utf-8") as fh:
        for i in range(2000):
            fh.write(_line(f"2026-03-02 10:00:{i % 60:02d}.000 UTC", user=f"u{i}"))
    cut = tmp_path / "postgresql-1.json.gz"
    data = whole.read_bytes()
    cut.write_bytes(data[: len(data) // 2])  # arquivo ainda em gravação
    report = ReadReport()

    records = list(read_records([cut], report))

    assert 0 < len(records) < 2000 and report.truncated >= 1
