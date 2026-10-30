# 01 - Python and tooling for .NET developers

Most AI tooling is Python-first. You do not need to love it; you need to read it fluently. This module maps
what you know from C#/.NET onto what you will see in this repository.

## Concept map

| You know (.NET) | In this repo (Python) | Where to look |
|---|---|---|
| `record` / POCO | `@dataclass` | `llm/types.py`, `ml/fraud.py` (`FraudModel`) |
| DataAnnotations / FluentValidation | Pydantic `BaseModel` with `Field(...)` | `agent/schemas.py`, `api/main.py` |
| ASP.NET Core minimal APIs | FastAPI (`@app.get`, `@app.post`) | `api/main.py` |
| `IOptions<T>` / appsettings + env vars | frozen dataclass read from env | `config.py` |
| Interfaces | `typing.Protocol` (structural typing) | `llm/providers.py` (`LLMProvider`) |
| LINQ over collections | list/dict comprehensions | everywhere: `[x for x in xs if ...]` |
| LINQ over data / DataTable | pandas `DataFrame` | `platform/lakehouse.py`, `ml/*` |
| EF Core + SQL | DuckDB / SQLite with plain SQL | `platform/warehouse.py`, `storage.py` |
| NuGet + .csproj | pip + `requirements.txt`, `pyproject.toml` | repo root |
| xUnit / NUnit | pytest (plain `assert`, fixtures) | `tests/` |
| `Lazy<T>` / singleton DI | `@lru_cache(maxsize=1)` on a factory | `get_settings()`, `get_router()`, `get_scorer()` |
| `Task.WhenAll` | `ThreadPoolExecutor.map` | `agent/investigator.py` (`tools` node) |
| `using` / `IDisposable` | `with` context managers | `storage.connect()` |
| Exceptions + filters | exceptions + FastAPI exception handlers | `api/main.py` |

## Python idioms you will meet

```python
def complete(self, *, task: str, system: str, tools: list[ToolSpec] | None = None) -> LLMResponse:
```
- `*` means everything after it must be passed by name (`complete(task="x", system="y")`).
- `list[ToolSpec] | None` is a type hint (like `List<ToolSpec>?`). Python does not enforce hints at runtime;
  Pydantic does, which is why external data always goes through a Pydantic model.

```python
state.setdefault("evidence", {})["fraud_score"] = result
```
- dictionaries are the workhorse data structure; `setdefault` = "get or insert default".

```python
@lru_cache(maxsize=1)
def get_router() -> LLMRouter: ...
```
- a decorator wraps a function; this one turns a factory into a process-wide singleton.

## pandas in five lines

```python
df = pd.read_parquet("transactions.parquet")          # load a table
recent = df[df.event_time >= "2026-08-01"]             # filter (like Where)
daily = recent.groupby(recent.event_time.dt.date).amount.sum()   # GroupBy + Sum
df["is_big"] = df.amount > 500                         # add a computed column
df.sort_values("amount", ascending=False).head(10)     # OrderByDescending + Take
```

## Tooling

- Virtual environments isolate dependencies per project: `python -m venv .venv` then activate it
  (`.venv\Scripts\Activate.ps1` on Windows). Optional here, recommended in real work.
- `python -m pytest` runs tests; `python -m pytest tests/test_rag.py -k timeline -q` runs one.
- `ruff check sentinel` lints (the CI pipeline runs it).
- Notebooks (Jupyter) are popular for exploration. This repo deliberately uses plain modules: production code
  lives in modules, and you can still explore interactively with `python -i` or a notebook that imports them.

## Exercises

1. Open `sentinel/config.py`. Add a new setting `case_queue_size` (default 24) read from an env var, and use it
   in `cli.py` `cmd_seed_cases` instead of the hard-coded 24. Run `seed-cases` with the env var set.
2. In `tests/test_guardrails.py`, add a test that a UK-style phone number is (or is not) redacted. Decide which
   behaviour you want and make the code match.
3. Read `api/main.py`. Add `GET /api/cases/{case_id}/audit` returning only the audit rows. Test it in the
   browser at `/docs` (FastAPI's interactive Swagger UI).

## Check yourself

- Why does external input go through Pydantic models even though Python has type hints?
- What does `@lru_cache(maxsize=1)` give you, and what is the risk (hint: tests, configuration changes)?
- Why are API endpoints in this repo `def` and not `async def`?
