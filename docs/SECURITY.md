# Security model

## Generated-code execution

`PythonExecutor` is a local development executor, not a security boundary. It provides:

- a fresh, validated artifact directory per run;
- Python isolated mode (`-I`);
- a minimal environment without inherited secrets;
- wall-clock timeout and captured stdout/stderr;
- explicit artifact enumeration.

It does **not** provide kernel-level CPU or memory quotas, filesystem isolation outside the working directory, syscall filtering, or reliable network denial. Never expose it to untrusted users. A production deployment must place each generated program in a disposable, non-root, network-disabled container or microVM with read-only base filesystems and CPU/memory/process quotas.

## API and artifacts

- Request bodies have explicit length constraints.
- Artifact retrieval uses an integer index and verifies the resolved file remains below the configured artifact root.
- The API CORS allowlist is explicit and environment-configurable.
- Background workers are bounded by `RESEARCHPILOT_WORKERS`.
- Accepted jobs and bounded retry state are persisted in SQLite and recovered on restart.
- Research state never stores LLM API keys.
- Setting `RESEARCHPILOT_API_TOKEN` enables constant-time bearer-token checks on research routes.

The built-in token is a service-level authentication foundation, not multi-tenant authorization. Bind the API to localhost during development. Shared deployments should terminate TLS at an authenticated gateway, map users to isolated workspaces, and enforce RBAC before requests reach ResearchPilot.

### Optional isolated API mode

Setting `RESEARCHPILOT_IDENTITIES_FILE` enables separate per-subject databases, queues, RAG stores, artifacts and traces, with reader/researcher roles. This mode requires the Docker execution backend. Credentials are either SHA-256 hashes of high-entropy bearer tokens or explicit mappings from verified issuer subjects. Optional RS256 JWT validation checks signature, issuer, audience, expiry, issued-at time and required claims using a configured HTTPS JWKS endpoint. Token-provided role claims are not trusted. The frontend JavaScript client supports in-memory bearer tokens, but the browser workspace does not expose token entry; OAuth redirect login and live IdP integration remain open. Existing-subject mappings and token revocation reload on new requests; invalid configuration fails closed. Progress streams revalidate authorization before each update batch and close when authorization ends. New workspaces and first-time verifier setup require restart. TLS, operational rate limits and organizational policies remain deployment requirements.

The Docker image manager removes only an unchanged alias recorded as owned by its workspace. It never performs global pruning or implicit image pulls during code execution. Offline tests check container command policy and cleanup; broader runtime isolation validation remains open.

## Scholarly content

Paper text and retrieved metadata are untrusted data. They may be evidence but never agent instructions. Literature claims require verified identifiers plus a persisted supporting passage and support score; quantitative observations require completed experiment IDs and can be checked against recorded values. Exported trace snapshots redact secret-like fields.
