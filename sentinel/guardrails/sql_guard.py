"""SQL safety for LLM-generated (text-to-SQL) queries.

Letting a model write SQL against a bank warehouse is powerful and dangerous. Layers used here:

  1. Static validation (this module): exactly one statement, SELECT/WITH only, no dangerous keywords,
     only allow-listed tables, and a row LIMIT is enforced.
  2. Least privilege at runtime: queries run on a READ-ONLY DuckDB connection (warehouse.py), so even a
     query that slipped past validation cannot modify data.
  3. Scoping: the agent's tool adds the customer filter itself rather than trusting the model with it.

A production version would parse SQL into an AST (e.g. sqlglot) instead of using regexes; the checks are
the same.
"""

from __future__ import annotations

import re

FORBIDDEN = {"insert", "update", "delete", "drop", "alter", "create", "replace", "truncate", "attach", "detach",
             "copy", "export", "import", "pragma", "install", "load", "call", "set", "grant", "revoke", "vacuum",
             "checkpoint", "merge", "upsert"}
FORBIDDEN_FUNCS = {"read_csv", "read_parquet", "read_json", "read_text", "read_blob", "glob", "httpfs", "getenv"}


class UnsafeSQLError(ValueError):
    pass


def _strip_comments_and_strings(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


def validate(sql: str, allowed_tables: set[str], max_rows: int = 200) -> str:
    """Return a safe, LIMITed version of `sql` or raise UnsafeSQLError with the reason."""
    s = sql.strip().rstrip(";").strip()
    bare = _strip_comments_and_strings(s).lower()
    if ";" in bare:
        raise UnsafeSQLError("multiple statements are not allowed")
    if not re.match(r"^\s*(select|with)\b", bare):
        raise UnsafeSQLError("only SELECT queries are allowed")
    words = set(re.findall(r"[a-z_]+", bare))
    bad = words & FORBIDDEN
    if bad:
        raise UnsafeSQLError(f"forbidden keyword(s): {sorted(bad)}")
    bad_fn = words & FORBIDDEN_FUNCS
    if bad_fn:
        raise UnsafeSQLError(f"forbidden function(s): {sorted(bad_fn)}")
    cte_names = set(re.findall(r"\b([a-z_][a-z0-9_]*)\s+as\s*\(", bare))
    # Table names follow FROM/JOIN. The negative lookahead skips `extract(day FROM col)`-style usages.
    # The first lookahead forbids matching part of an identifier (e.g. "f." out of "f.event_time").
    referenced = set(re.findall(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?)(?![a-z0-9_.])(?!\s*\))", bare))
    unknown = {t for t in referenced if t not in allowed_tables and t not in cte_names}
    if unknown:
        raise UnsafeSQLError(f"table(s) not allowed: {sorted(unknown)}; allowed: {sorted(allowed_tables)}")
    return f"SELECT * FROM ({s}) AS q LIMIT {int(max_rows)}"
