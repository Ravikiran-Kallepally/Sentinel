# 10 - Serving, deployment and MLOps

## The API - `api/main.py`

- FastAPI with Pydantic request models (validation at the edge) and interactive docs at `/docs`.
- Endpoints are plain `def`: blocking library work runs in FastAPI's thread pool.
- Errors map to HTTP codes in one place (exception handlers).
- Optional API key (`SENTINEL_API_KEY`) and analyst identity header (`X-Analyst`). Production: SSO at the gateway,
  role-based access per endpoint, and identity from the token rather than a header.

## Containers - `Dockerfile`, `docker-compose.yml`

- Slim base image, CPU-only PyTorch, non-root user, health check.
- `scripts/entrypoint.sh` bootstraps on first start (empty volume), then serves.
- One worker by default: the demo keeps the replay cursor and semantic cache in memory and uses SQLite. Scaling out
  first requires moving that state to Redis/Postgres - see `next-steps.md`.

```bash
docker compose up --build        # http://localhost:8000
```

## Kubernetes - `k8s/`

- `sentinel.yaml`: namespace, config map, secret reference, PVC, deployment with probes, resource requests/limits and
  a non-root security context, service, HPA (pinned to one replica until state is external).
- `jobs.yaml`: a bootstrap Job and a nightly CronJob that runs pipeline → train → eval → gate. The registry's
  champion/challenger rule means a nightly retrain only replaces production when it is better.

## CI/CD - `.github/workflows/ci.yml`

lint → unit tests → full bootstrap (offline, deterministic) → **release gate** → container build. Eval reports are
uploaded as artifacts. Extend with: push to a registry, deploy to staging, smoke tests, canary to production.

## Infrastructure as code - `infra/terraform/main.tf`

A sketch of the AWS resources: encrypted, versioned, private S3 for the lake; ECR with scanning and immutable tags;
Secrets Manager for LLM keys. The header comment maps each local stand-in to its managed service.

## MLOps loop

```
data pipeline ─▶ train ─▶ register (champion/challenger) ─▶ evaluate ─▶ gate ─▶ serve ─▶ monitor drift + outcomes
      ▲                                                                                        │
      └──────────────────────────────── retrain on schedule or drift ◀──────────────────────────┘
```

## Scaling and reliability discussion points

- State: what has to move out of the process before you can run more than one replica?
- Latency budget: real-time scoring must be milliseconds; agent investigations take seconds - different services,
  different scaling, probably a job queue for the agent.
- Rate limits and quotas from LLM providers: retries with backoff, request queues, multiple deployments/regions.
- Blue-green or canary releases for models *and* prompts.
- Disaster recovery: the bronze layer and the registry let you rebuild everything else.

## Exercises

1. Build and run the container. Kill it mid-bootstrap, start it again - what happens, and is it safe?
2. Run on a local Kubernetes cluster (kind or minikube): load the image, apply `k8s/sentinel.yaml` and
   `k8s/jobs.yaml`, port-forward, and open the UI.
3. Add a `/api/ready` endpoint that is only healthy once models and the index are loaded, and use it as the readiness
   probe (why is that better than `/api/health`?).
4. Move the semantic cache to Redis behind the same interface (`get`/`put`/`clear`). What else must move before
   you can raise `replicas`?
5. Add a CI job that runs the RAG eval against a real model nightly and posts the metrics.

## Check yourself

- Why does the API run with one worker, and what would you change to scale it?
- Why is the release gate in CI rather than a manual checklist?
- What is the difference between liveness and readiness probes?
- Which state lets you rebuild the platform after a disaster?
