#!/usr/bin/env bash
# Dependency vulnerability scan over the exact versions pinned in uv.lock.
# Used by `make audit` and CI. Fails on any advisory not explicitly accepted below.
set -euo pipefail

# Accepted advisories - each one is justified in SECURITY.md ("Accepted dependency advisories").
# Re-review whenever chromadb is upgraded or the deployment model changes.
ACCEPTED=(
  PYSEC-2026-311   # CVE-2026-45829  chromadb server: remote code via collection endpoint (server mode only)
  PYSEC-2026-3814  # CVE-2026-45833  chromadb server: remote code via collection update (server mode only)
  PYSEC-2026-3815  # CVE-2026-45831  chromadb server: SimpleRBAC ignores tenant scope (server mode only)
  PYSEC-2026-3813  # CVE-2026-45830  chromadb server: missing tenant authorisation (server mode only)
)

requirements="$(mktemp)"
trap 'rm -f "$requirements"' EXIT
uv export --quiet --frozen --no-dev --all-extras --no-hashes --no-emit-project \
  --format requirements-txt -o "$requirements"

ignore_args=()
for id in "${ACCEPTED[@]}"; do ignore_args+=(--ignore-vuln "$id"); done

uv run --no-sync pip-audit -r "$requirements" --disable-pip --no-deps --progress-spinner off "${ignore_args[@]}"
