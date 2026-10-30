"""A small data-quality framework (the ideas behind Great Expectations / dbt tests / Deequ).

Two kinds of checks matter in practice:

* Dataset-level checks decide whether a whole batch may be promoted (e.g. "row count did not drop by
  more than 50%", "data is fresh"). A failing dataset check blocks the pipeline - better no data than
  wrong data.
* Row-level checks find individual bad rows (e.g. negative amount). Bad rows are *quarantined*, not
  silently dropped, so someone can inspect and replay them.

Every result is written to the ops database so the UI can show DQ history.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pandas as pd

from sentinel import storage


@dataclass
class CheckResult:
    name: str
    passed: bool
    observed: str
    blocking: bool


@dataclass
class Check:
    """A named predicate over a DataFrame.

    `row_mask` (optional) returns a boolean Series marking *bad* rows; those rows get quarantined.
    `blocking` checks stop promotion of the batch when they fail.
    """

    name: str
    fn: Callable[[pd.DataFrame], tuple[bool, str]]
    blocking: bool = True
    row_mask: Callable[[pd.DataFrame], pd.Series] | None = None


def not_null(col: str) -> Check:
    def fn(df):
        n = int(df[col].isna().sum())
        return n == 0, f"{n} nulls"
    return Check(f"not_null({col})", fn, blocking=False, row_mask=lambda df: df[col].isna())


def unique(col: str) -> Check:
    def fn(df):
        d = int(df[col].duplicated().sum())
        return d == 0, f"{d} duplicates"
    return Check(f"unique({col})", fn)


def between(col: str, lo: float | None, hi: float | None) -> Check:
    def bad(df):
        s = df[col]
        m = pd.Series(False, index=df.index)
        if lo is not None:
            m |= s < lo
        if hi is not None:
            m |= s > hi
        return m

    def fn(df):
        n = int(bad(df).sum())
        return n == 0, f"{n} out of range [{lo}, {hi}]"
    return Check(f"between({col},{lo},{hi})", fn, blocking=False, row_mask=bad)


def in_set(col: str, allowed: set) -> Check:
    def bad(df):
        return ~df[col].isin(allowed)

    def fn(df):
        n = int(bad(df).sum())
        return n == 0, f"{n} unexpected values"
    return Check(f"in_set({col})", fn, blocking=False, row_mask=bad)


def min_rows(n: int) -> Check:
    return Check(f"min_rows({n})", lambda df: (len(df) >= n, f"{len(df)} rows"))


def reconcile(expected: int, tolerance: float = 0.0, label: str = "source") -> Check:
    """Row-count reconciliation between layers (e.g. bronze distinct ids vs silver rows)."""
    def fn(df):
        diff = abs(len(df) - expected) / max(expected, 1)
        return diff <= tolerance, f"{len(df)} vs {label} {expected} (diff {diff:.2%})"
    return Check(f"reconcile({label})", fn)


@dataclass
class SuiteReport:
    dataset: str
    results: list[CheckResult] = field(default_factory=list)
    quarantined: int = 0

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results if r.blocking)

    def as_dict(self) -> dict:
        return {"dataset": self.dataset, "passed": self.passed, "quarantined": self.quarantined,
                "checks": [r.__dict__ for r in self.results]}


def run_suite(dataset: str, df: pd.DataFrame, checks: list[Check], run_id: str) -> tuple[SuiteReport, pd.DataFrame, pd.DataFrame]:
    """Run checks; return (report, good_rows, quarantined_rows)."""
    report = SuiteReport(dataset)
    bad = pd.Series(False, index=df.index)
    for c in checks:
        ok, observed = c.fn(df)
        report.results.append(CheckResult(c.name, bool(ok), observed, c.blocking))
        if c.row_mask is not None:
            bad |= c.row_mask(df).fillna(True)
    report.quarantined = int(bad.sum())
    ts = datetime.now(UTC).isoformat()
    with storage.connect() as con:
        con.executemany(
            "INSERT INTO dq_results(run_id, dataset, check_name, passed, observed, ts) VALUES (?,?,?,?,?,?)",
            [(run_id, dataset, r.name, int(r.passed), r.observed, ts) for r in report.results],
        )
    return report, df[~bad], df[bad]


class DataQualityError(RuntimeError):
    """Raised when a blocking check fails. The pipeline task fails and nothing is promoted."""
