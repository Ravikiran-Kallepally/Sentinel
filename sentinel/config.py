"""Central configuration.

Every tunable lives here and is read from environment variables (or a .env file), so the
same code runs on a laptop, in Docker and in Kubernetes. This is the "12-factor app" idea:
config is environment, not code.

Design note: settings are a frozen dataclass built once by `get_settings()`. Modules call
`get_settings()` instead of reading os.environ themselves, so there is one place to look
when you want to know what can be configured.
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

try:  # python-dotenv is optional; without it we just use real env vars
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# joblib/loky tries to count physical cores with a shell tool that is missing on some Windows machines and
# prints a noisy traceback; giving it the count avoids that.
os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(max(1, (os.cpu_count() or 2) - 1)))
warnings.filterwarnings("ignore", message="Could not find the number of physical cores")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens. Used for cost accounting, not billing."""

    input: float
    output: float


# Prices for cost tracking. Anthropic first-party list prices; OpenAI entries are placeholders
# you should update for the models you actually configure.
DEFAULT_PRICES: dict[str, ModelPrice] = {
    "claude-opus-5-5": ModelPrice(4.00, 20.00),
    "claude-sonnet-5-5": ModelPrice(2.00, 10.00),
    "claude-haiku-5-5": ModelPrice(0.10, 0.50),
    "offline": ModelPrice(0.0, 0.0),
}


@dataclass(frozen=True)
class Settings:
    # ---- paths -------------------------------------------------------------
    home: Path = field(default_factory=lambda: Path(_env("SENTINEL_HOME", str(PROJECT_ROOT / "var"))))

    # ---- data generation ----------------------------------------------------
    seed: int = field(default_factory=lambda: _env_int("SENTINEL_SEED", 7))
    n_customers: int = field(default_factory=lambda: _env_int("SENTINEL_N_CUSTOMERS", 1500))
    n_days: int = field(default_factory=lambda: _env_int("SENTINEL_N_DAYS", 120))

    # ---- LLM -----------------------------------------------------------------
    # auto      -> anthropic if ANTHROPIC_API_KEY is set, else openai if OPENAI_API_KEY, else offline
    # anthropic -> Claude via the Anthropic SDK (also picks up an `ant auth login` profile)
    # openai    -> OpenAI; azure -> Azure OpenAI; offline -> deterministic local stand-in
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "auto"))
    # Two tiers: "strong" for planning / final summaries, "fast" for cheap high-volume tasks.
    anthropic_model_strong: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL_STRONG", "claude-opus-5-5"))
    anthropic_model_fast: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL_FAST", "claude-haiku-5-5"))
    openai_model_strong: str = field(default_factory=lambda: _env("OPENAI_MODEL_STRONG", "gpt-4o"))
    openai_model_fast: str = field(default_factory=lambda: _env("OPENAI_MODEL_FAST", "gpt-4o-mini"))
    azure_deployment_strong: str = field(default_factory=lambda: _env("AZURE_OPENAI_DEPLOYMENT_STRONG", ""))
    azure_deployment_fast: str = field(default_factory=lambda: _env("AZURE_OPENAI_DEPLOYMENT_FAST", ""))
    llm_timeout_s: float = field(default_factory=lambda: _env_float("LLM_TIMEOUT_S", 120.0))
    llm_max_retries: int = field(default_factory=lambda: _env_int("LLM_MAX_RETRIES", 2))

    # ---- RAG -----------------------------------------------------------------
    chunk_tokens: int = field(default_factory=lambda: _env_int("RAG_CHUNK_TOKENS", 220))
    chunk_overlap: int = field(default_factory=lambda: _env_int("RAG_CHUNK_OVERLAP", 40))
    retrieve_k: int = field(default_factory=lambda: _env_int("RAG_RETRIEVE_K", 20))
    rerank_k: int = field(default_factory=lambda: _env_int("RAG_RERANK_K", 5))
    embedding_backend: str = field(default_factory=lambda: _env("EMBEDDING_BACKEND", "lsa"))  # lsa | sentence-transformers
    # Off by default: on our golden set the rule-based rewrite LOWERED MRR (0.955 -> 0.902). Measure before enabling.
    query_rewrite: bool = field(default_factory=lambda: _env("RAG_QUERY_REWRITE", "false").lower() == "true")

    # ---- agent ---------------------------------------------------------------
    agent_max_steps: int = field(default_factory=lambda: _env_int("AGENT_MAX_STEPS", 8))
    agent_max_cost_usd: float = field(default_factory=lambda: _env_float("AGENT_MAX_COST_USD", 0.50))

    # ---- fraud decisioning ----------------------------------------------------
    review_cost_usd: float = field(default_factory=lambda: _env_float("FRAUD_REVIEW_COST_USD", 6.0))

    # ---- semantic cache ------------------------------------------------------
    cache_similarity: float = field(default_factory=lambda: _env_float("CACHE_SIMILARITY", 0.92))

    prices: dict[str, ModelPrice] = field(default_factory=lambda: dict(DEFAULT_PRICES))

    # ---- derived paths ---------------------------------------------------------
    @property
    def sources_dir(self) -> Path:  # "source systems" the platform ingests from
        return self.home / "sources"

    @property
    def lake_dir(self) -> Path:  # bronze / silver / gold parquet
        return self.home / "lake"

    @property
    def warehouse_path(self) -> Path:  # DuckDB file standing in for Snowflake
        return self.home / "warehouse.duckdb"

    @property
    def models_dir(self) -> Path:  # model registry
        return self.home / "models"

    @property
    def index_dir(self) -> Path:  # RAG index
        return self.home / "index"

    @property
    def ops_db(self) -> Path:  # cases, audit log, traces, pipeline runs (SQLite)
        return self.home / "ops.sqlite"

    @property
    def reports_dir(self) -> Path:
        return self.home / "reports"

    @property
    def corpus_dir(self) -> Path:  # policy documents shipped with the repo
        return PROJECT_ROOT / "sentinel" / "data" / "corpus"

    def ensure_dirs(self) -> None:
        for p in (self.home, self.sources_dir, self.lake_dir, self.models_dir, self.index_dir, self.reports_dir):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
