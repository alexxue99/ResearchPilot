# Backend configuration reference

For installation and the default local workflow, start with the [README](../README.md) or [downloadable setup guide](../frontend/public/demos/local-setup.md).

## Configuration and workspaces

The API and CLI load `.env.local` from the selected workspace, or from the repository root when the workspace is named `.researchpilot`. Explicit process environment variables take precedence. A custom workspace can have its own `.env.local`.

`RESEARCHPILOT_MODE` accepts Full, Limited, or Restricted. Model selection and request timeouts follow the [mode table](../frontend/public/demos/local-setup.md#choose-a-research-mode). The `RESEARCHPILOT_LIMITED_*` and `RESEARCHPILOT_RESTRICTED_*` settings bound steps, papers, tools, tokens, estimated cost, and daily runs. Experiment execution requires Docker and is disabled in Restricted mode.

Runtime databases, papers, outputs, reports, and traces live under `.researchpilot/`. Curated ZIPs in `demos/` and gallery files in `frontend/public/demos/` are separate, versioned assets.

## Docker images and execution

Start the Docker engine in Linux-container mode. Prepare the default image explicitly:

```powershell
python -m researchpilot.cli images prepare --image python:3.13-slim
python -m researchpilot.cli images inspect --image python:3.13-slim
```

Set `RESEARCHPILOT_EXECUTOR=docker` and `RESEARCHPILOT_DOCKER_IMAGE` to the official Python tag/digest or the workspace-owned alias returned by `prepare`. Preparation pulls the official image, checks its Linux OS and immutable ID, and stores an ownership receipt. Execution uses `--pull never` and does not install packages or fetch images. Host Python extras do not change the container environment.

The executor uses a disposable non-root container, disabled networking, a read-only root filesystem, a writable output directory, and resource limits. Timeout cleanup removes only the named container. The local subprocess executor is for trusted development and is not a sandbox; the conjecture pipeline requires Docker for generated experiments.

To retire an alias owned by this workspace:

```powershell
python -m researchpilot.cli images remove --image researchpilot-executor:OWNER-IMAGE
```

Use the exact alias from its receipt. Removal refuses foreign or changed aliases and does not prune shared Docker images. See the [security model](SECURITY.md).

## Paper ingestion and retrieval

PDF, text, HTML, and Markdown ingestion retains source, page, section, and chunk provenance. PDF extraction is heuristic; scanned papers may need OCR and equations or columns may be misread.

Downloads require public HTTPS URLs on exact hosts configured by `RESEARCHPILOT_PAPER_HOSTS`. Redirects, public network addresses, size, and deadlines are checked. A successful download does not establish that a paper belongs to a claimed DOI. Source identity and quoted passages still need review.

The default embedding provider uses dependency-free hashing. For local semantic embeddings, install the `embeddings` extra and set:

```dotenv
RESEARCHPILOT_EMBEDDINGS=sentence-transformer
RESEARCHPILOT_EMBEDDING_MODEL=C:/models/my-embedding-model
RESEARCHPILOT_EMBEDDING_REVISION=explicit-model-revision
```

Provision weights separately. The adapter never implicitly downloads models or enables remote model code. Re-ingest papers after changing the embedding model; vectors from different model versions are not mixed. Keep the weight directory immutable for its revision.

## PDF reports

The web app can download a report as a LaTeX PDF. The API needs XeLaTeX and TeX Gyre fonts; the backend Dockerfile installs them. Markdown reports and demo ZIP exports work without a TeX installation. The backend image is separate from the executor image.

## Prices and telemetry

`RESEARCHPILOT_PRICE_TABLE` points to an operator-maintained USD JSON table, keyed by provider and exact model name:

```json
{
  "as_of": "2026-10-03",
  "currency": "USD",
  "providers": {
    "openai": {
      "example-model": {
        "input_per_million": 2.0,
        "output_per_million": 8.0,
        "cached_input_per_million": 0.5
      }
    }
  }
}
```

These example rates are fictional. Review the dated bundled `prices.json` and replace entries with verified rates for your configured models. Explicit `OPENAI_INPUT_COST_PER_MILLION` and `OPENAI_OUTPUT_COST_PER_MILLION` override the table together; cached-input pricing is optional. Unknown pricing marks estimates incomplete. Bounded modes require known prices; estimated costs are not a provider billing statement.

Install the `telemetry` extra and set `RESEARCHPILOT_OTEL=1` for best-effort OTLP/HTTP export. Standard `OTEL_EXPORTER_OTLP_*` settings select the collector. Operational spans exclude prompts, paper contents, and tool inputs/outputs. Deduplication is per process and resets on restart.

## Authentication and isolated workspaces

`RESEARCHPILOT_API_TOKEN` enables a service-level bearer token. The frontend JavaScript client supports in-memory tokens, but the browser workspace does not currently expose token entry or an OAuth login flow.

`RESEARCHPILOT_IDENTITIES_FILE` enables reader/researcher roles and physically separate per-subject databases, jobs, RAG stores, artifacts, and traces. This mode requires Docker. Credential records use hashed high-entropy bearer tokens or issuer-subject mappings for RS256 JWTs. Install the `identity` extra for JWT verification and explicitly configure `RESEARCHPILOT_OIDC_ISSUER`, `RESEARCHPILOT_OIDC_AUDIENCE`, and `RESEARCHPILOT_OIDC_JWKS_URL`.

Roles come from local configuration, not token claims. Existing-subject mappings reload on new requests; invalid configuration fails closed, and progress streams revalidate authorization. New workspace provisioning or first-time verifier setup requires restart. Shared deployments also need TLS and operational access controls.

## Standalone workers

Set `RESEARCHPILOT_WORKERS=0` to disable embedded API workers, then run:

```powershell
python -m researchpilot.cli --workspace .researchpilot worker
```

`--once` claims at most one ready job. In isolated multi-user mode, point a worker at the selected subject's hashed workspace, not the parent root.

Jobs use atomic claims, leases, heartbeats, bounded retries, fenced writes, and attempt-specific output directories. This is a single-host SQLite queue: use a local filesystem. Execution is at least once; retries can rerun computation, and lease loss prevents stale state publication without immediately terminating the stale computation.

## Validation

Use the test commands in the [README](../README.md#verification). Optional identity and telemetry dependencies enable their integration tests. Docker runtime smoke tests require explicit opt-in. Offline fixture tests do not establish live provider quality, publisher availability, organizational SSO integration, or distributed execution.

The CLI's historical `benchmark` command evaluates tool-selection heuristics only. The former deterministic scientific-gold and end-to-end benchmark runners have been removed because their execution paths no longer match the conjecture pipeline. For a real saved investigation, use `python -m researchpilot.cli evaluate RESEARCH_ID` to inspect grounding and experiment-quality diagnostics.
