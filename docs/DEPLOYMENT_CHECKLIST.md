# Deployment readiness checklist

Use this before running SecureAgentOps anywhere beyond a local demo. Items marked ✅ are already handled by
the repository; the rest are deployment decisions or production work.

## 1. Local / demo deployment (mock mode)

- [ ] `make env` (or copy `.env.example` → `.env` and replace every placeholder)
- [ ] `make check` passes: lint, 97 tests, dependency audit
- [ ] `docker compose up --build -d`, then both services report `healthy` in `docker compose ps`
- [ ] `curl localhost:8000/ready` → `{"status":"ok", ...}`
- [ ] Sign in with a demo account and run the [demo script](../README.md#demo-script-5-minutes)
- [ ] `docker compose down` when finished (`docker compose down -v` also deletes demo data)

## 2. Configuration

- [ ] `SAO_ENVIRONMENT=production` (enables startup guards and disables `/docs`)
- [ ] `SAO_JWT_SECRET` from a secrets manager, 32+ random characters — ✅ enforced at startup
- [ ] `SAO_SEED_DEMO_DATA=false` — ✅ enforced at startup in production
- [ ] `SAO_ALLOW_SELF_REGISTRATION` set deliberately (false for invite-only)
- [ ] `SAO_ACCESS_TOKEN_MINUTES` reviewed (shorter is safer)
- [ ] `SAO_CORS_ORIGINS` set only if a browser client on another origin calls the API
- [ ] Rate limits sized for expected traffic
- [ ] Upload limit (`SAO_MAX_UPLOAD_MB`) agreed and mirrored in the dashboard (`STREAMLIT_SERVER_MAX_UPLOAD_SIZE`)

## 3. Model provider (if using Bedrock)

- [ ] **Explicit approval to incur AWS charges**, and an AWS Budget alarm on the account
- [ ] Model access enabled for `SAO_BEDROCK_MODEL_ID` in `SAO_AWS_REGION`
- [ ] IAM role with least privilege: `bedrock:InvokeModel` on the specific model ARNs only
- [ ] `SAO_PRICE_*` set from the current price list if cost estimates are wanted
- [ ] If switching embeddings to Titan: set `SAO_EMBEDDING_DIM` to 256/512/1024 and re-index all documents
- [ ] Smoke test against the live model; record that it was verified (the repo only tests Bedrock offline)

## 4. Data and isolation

- [ ] Replace SQLite with PostgreSQL; add row-level security on `tenant_id`
- [ ] Replace embedded Chroma with a managed vector store (see ARCHITECTURE.md §7)
- [ ] Encryption at rest for database, vector store and volumes
- [ ] Backups with a tested restore
- [ ] Data-retention jobs for runs, tool invocations and documents
- [ ] ✅ Tenant-isolation tests pass (`tests/test_tenant_isolation.py`); add equivalent tests for any new data store

## 5. Network and edge

- [ ] TLS termination (ALB or API Gateway) — never expose Uvicorn directly
- [ ] WAF rules and gateway throttling
- [ ] `--forwarded-allow-ips` set to the load balancer so client IPs are correct
- [ ] `/metrics` reachable only from the monitoring network
- [ ] Data stores in private subnets; VPC endpoints for Bedrock and Secrets Manager

## 6. Scaling

- [ ] Move rate limiting to Redis or the gateway **before** running more than one API worker or replica
- [ ] Move vector storage off the API container before scaling horizontally
- [ ] Dashboard and API scaled independently

## 7. Observability

- [ ] `SAO_OTEL_EXPORTER_OTLP_ENDPOINT` points at a collector (ADOT, Grafana Agent, …)
- [ ] Prometheus scrapes `/metrics`; dashboards for latency p95, tool failures, retrieval latency, tokens
- [ ] Alerts: `/ready` failing, 5xx rate, `sao_tool_invocations_total{outcome="timeout"}`, `sao_rate_limited_total` spikes
- [ ] Logs shipped centrally; audit events exported to tamper-evident storage

## 8. Security sign-off

- [ ] ✅ CI green: lint (incl. bandit rules), tests, pip-audit, gitleaks, Docker smoke test
- [ ] Accepted advisories in SECURITY.md re-reviewed for the deployed versions
- [ ] Container image scanned and signed; base image pinned by digest
- [ ] OIDC/SSO and MFA in place of local passwords (or a documented exception)
- [ ] Penetration test or security review completed
- [ ] Incident-response contact and runbook for the platform itself

## 9. Release

- [ ] Version bumped (`SAO_APP_VERSION` / `pyproject.toml`) and changelog written
- [ ] Database migrations planned (the demo uses `create_all`; production should use Alembic)
- [ ] Rollback plan tested
