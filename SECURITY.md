# Security

SecureAgentOps is a portfolio project designed with production security patterns. It has **not** had an
independent security review and should not hold real customer data without the changes in
[Before production](#before-production).

## Reporting

Please report suspected vulnerabilities privately to the repository owner rather than in a public issue.

## Threat model

| Asset | Threat | Primary controls |
|---|---|---|
| Tenant documents | Cross-tenant retrieval through the API or a crafted prompt | Tenant from verified token + DB; `TenantRepository`; per-tenant Chroma collections, metadata filter and post-query check; tests in `tests/test_tenant_isolation.py` |
| Tool actions | The model or a user triggers a state change without authority | Code-only policy engine; fixed intent→tool mapping; strict argument schemas; human approval with separation of duties, expiry and compare-and-set |
| Accounts | Credential stuffing, enumeration, weak passwords | Argon2id; generic login errors; timing-equalised verification; login and registration rate limits; password policy; audit of failed logins |
| Tokens | Forgery, algorithm confusion, replay after deactivation | HS256 pinned; issuer, audience, expiry and required claims checked; user and tenant re-validated per request |
| Service | Resource exhaustion | Per-user rate limits; message length cap; upload size cap read without buffering the whole stream; PDF page cap; tool and LLM timeouts |
| Internals | Information leakage in errors | Uniform error body; no stack traces, SQL or provider error text returned; validation errors omit submitted values |
| Supply chain | Vulnerable or malicious dependencies | `uv.lock` pins every version; pip-audit in CI; gitleaks secret scan; minimal runtime image |

## Controls

### Authentication and authorisation
- Passwords hashed with Argon2id (`argon2-cffi` defaults), transparently re-hashed when parameters change.
- Self-registration creates a **new** tenant only. Nobody can register into an existing tenant; admins add users.
- JWT verification pins the algorithm and requires `sub`, `tid`, `exp`, `iat`, `iss`, `aud`.
- Roles are read from the database on every request, not trusted from the token.
- `require_admin` guards user management, approval decisions, document deletion and the audit log.

### Tenant isolation
- No API, tool or prompt accepts a tenant id. It comes from the authenticated principal.
- `TenantRepository` refuses to exist without a tenant id and scopes every query and insert.
- Objects in another tenant return **404**, not 403, so their existence isn't revealed.
- Vector store: one collection per tenant, a metadata `where` filter, a format check on tenant ids, and
  post-query verification.
- Prometheus labels never include tenant or user ids.

### LLM and tool safety
- The LLM never makes authorisation decisions. Its output is a proposal that passes through a fixed
  intent→tool mapping, a strict schema (`extra="forbid"`) and the policy engine.
- Every non-read-only tool requires approval, even if its spec omits the flag.
- Retrieved text and user input are delimited and labelled untrusted in prompts. Tool outcomes are reported
  from structured data, not model text.
- The dashboard renders document snippets as plain text; Streamlit's Markdown rendering has HTML disabled.
- `create_incident` is simulated: it writes a local row and calls nothing external.

### Input handling
- Uploads: extension allow-list (`.pdf`, `.txt`, `.md`), size limit enforced from `Content-Length` and by
  reading at most limit+1 bytes, `%PDF-` magic-byte check, binary detection for text, UTF-8 only, encrypted PDFs
  rejected, 200-page cap, filename sanitised (no directories or special characters), duplicates detected per
  tenant by SHA-256.
- All request bodies use Pydantic models that reject unknown fields.

### Operational
- Security headers: `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Cache-Control: no-store`
  and a restrictive CSP on API responses.
- OpenAPI docs are disabled in production; production refuses to start without a 32+ character JWT secret,
  and refuses demo seeding.
- Containers run as a non-root user with a read-only root filesystem, `no-new-privileges` and all capabilities
  dropped; Compose binds ports to `127.0.0.1`.
- Audit events: registration, logins (including failures), user creation, document indexing and deletion,
  approval requests and decisions, incident creation.

### Secrets
- No secrets in source. `.env.example` holds placeholders only; `.env`, databases and credential files are
  git-ignored.
- `make env` generates the JWT secret and demo password locally with owner-only file permissions.
- AWS credentials come from the standard AWS chain, never from application config.
- The optional Docker build secret for a proxy CA is mounted for one step and never stored in a layer.

## Accepted dependency advisories

`scripts/audit.sh` fails the build on any advisory except these, which were reviewed on 2026-10-22:

| Advisory | Package | Why it doesn't apply here |
|---|---|---|
| PYSEC-2026-311 / CVE-2026-45829 | chromadb 1.5.9 | Remote code execution through Chroma's **HTTP server** collection endpoint. This project uses Chroma embedded in-process (`PersistentClient`); no Chroma server or port is exposed, and embedding functions are never configured from requests. |
| PYSEC-2026-3814 / CVE-2026-45833 | chromadb 1.5.9 | Same class of issue on the server's collection-update endpoint. Not exposed. |
| PYSEC-2026-3815 / CVE-2026-45831 | chromadb 1.5.9 | Chroma server's `SimpleRBACAuthorizationProvider` ignores tenant scope. Not used: isolation is enforced by this application, not Chroma's auth. |
| PYSEC-2026-3813 / CVE-2026-45830 | chromadb 1.5.9 | Missing tenant authorisation in Chroma **server** mode. Not used. |

No patched chromadb release existed at review time. **Re-review when chromadb is upgraded, and never run
Chroma in client/server mode with these versions.** The production plan replaces Chroma with a managed
vector store (see ARCHITECTURE.md §7).

## Known limitations

- **Rate limiting is in-memory and per process** (one worker by default). It resets on restart and isn't
  shared across replicas.
- **No token revocation list.** A stolen token is valid until expiry (default 60 minutes), unless the user is
  deactivated. No refresh tokens, MFA or SSO.
- **Prompt injection is mitigated, not solved.** It can't trigger unauthorised actions, but injected document
  text could still skew an answer's wording.
- **Tool timeouts** release the request but can't stop a running thread.
- **No malware scanning** of uploads; parsing untrusted PDFs carries parser risk (pypdf).
- **SQLite and the Chroma files aren't encrypted at rest** by the application; rely on volume encryption.
- **`/metrics` is unauthenticated** (standard for Prometheus scraping). Keep it on an internal network.
- **Client IPs:** the login limiter uses the direct peer address. Behind a proxy, configure trusted proxy
  headers (`--forwarded-allow-ips`) so all users don't share one bucket.
- **The audit log is not tamper-evident.**

## Before production

1. Managed secrets (AWS Secrets Manager) with rotation; consider asymmetric JWT signing (RS256/ES256) with a JWKS.
2. Replace local auth with OIDC (Cognito or your IdP), MFA, short-lived access plus refresh tokens, and revocation.
3. PostgreSQL with row-level security as a second tenant-isolation layer; encrypted storage; backups.
4. Managed vector store with tenant isolation; remove embedded Chroma.
5. Distributed rate limiting at the gateway (API Gateway usage plans or WAF) plus Redis per-user limits.
6. Upload malware scanning, per-tenant quotas, and sandboxed document parsing.
7. Bedrock Guardrails for content and PII filtering, and an evaluation suite for prompt-injection regressions.
8. TLS everywhere, WAF, private networking for data stores, VPC endpoints for Bedrock.
9. Centralised, tamper-evident audit logging (for example CloudWatch Logs with Object Lock exports).
10. Penetration test and a threat-model review; container image scanning and signing (for example ECR scanning + cosign).

See [docs/DEPLOYMENT_CHECKLIST.md](docs/DEPLOYMENT_CHECKLIST.md).
