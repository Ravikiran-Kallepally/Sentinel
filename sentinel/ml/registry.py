"""A file-based model registry (the core of what MLflow's Model Registry does).

Every training run registers a new *version* with its metrics, parameters and the data it was trained on.
Versions move through *stages*: a new version starts in `staging`; it is promoted to `production` only if
it beats the current production version on the primary metric (champion / challenger). Rollback = point
`production` back to an older version.

Layout:
    models/<name>/v<N>/model.joblib
    models/<name>/v<N>/meta.json
    models/<name>/registry.json      {"production": N, "staging": M, "versions": [...]}
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib

from sentinel.config import get_settings


def _dir(name: str) -> Path:
    d = get_settings().models_dir / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_reg(name: str) -> dict:
    p = _dir(name) / "registry.json"
    return json.loads(p.read_text()) if p.exists() else {"production": None, "staging": None, "versions": []}


def _save_reg(name: str, reg: dict) -> None:
    (_dir(name) / "registry.json").write_text(json.dumps(reg, indent=2, default=str))


def register(name: str, model: Any, metrics: dict, params: dict, primary_metric: str,
             higher_is_better: bool = True, data_ref: dict | None = None, notes: str = "") -> dict:
    """Save a new version and run the champion/challenger promotion rule."""
    reg = _load_reg(name)
    version = len(reg["versions"]) + 1
    vdir = _dir(name) / f"v{version}"
    vdir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, vdir / "model.joblib")
    meta = {"name": name, "version": version, "created_at": datetime.now(UTC).isoformat(), "metrics": metrics,
            "params": params, "primary_metric": primary_metric, "higher_is_better": higher_is_better,
            "data_ref": data_ref or {}, "notes": notes}
    (vdir / "meta.json").write_text(json.dumps(meta, indent=2, default=str))

    champion = reg["production"]
    promoted, reason = True, "first version"
    if champion is not None:
        champ_meta = get_meta(name, champion)
        old, new = champ_meta["metrics"].get(primary_metric), metrics.get(primary_metric)
        better = (new > old) if higher_is_better else (new < old)
        promoted = bool(better)
        reason = f"{primary_metric}: challenger {new:.4f} vs champion {old:.4f}"
    reg["versions"].append({"version": version, "created_at": meta["created_at"], primary_metric: metrics.get(primary_metric)})
    if promoted:
        reg["production"] = version
    else:
        reg["staging"] = version
    _save_reg(name, reg)
    return {"name": name, "version": version, "promoted": promoted, "reason": reason}


def get_meta(name: str, version: int | None = None) -> dict:
    reg = _load_reg(name)
    v = version or reg["production"]
    if v is None:
        raise FileNotFoundError(f"no production version of model '{name}' - run `python -m sentinel.cli train`")
    return json.loads((_dir(name) / f"v{v}" / "meta.json").read_text())


def load(name: str, version: int | None = None) -> Any:
    meta = get_meta(name, version)
    return joblib.load(_dir(name) / f"v{meta['version']}" / "model.joblib")


def promote(name: str, version: int) -> None:
    """Manual promotion / rollback."""
    reg = _load_reg(name)
    reg["production"] = version
    _save_reg(name, reg)


def list_models() -> list[dict]:
    root = get_settings().models_dir
    out = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []:
        reg = _load_reg(d.name)
        out.append({"name": d.name, **reg})
    return out
