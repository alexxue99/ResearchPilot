# Implementation status and remaining work

## Current capabilities

- A nine-stage conjecture pipeline with structured model responses, source selection, passage-grounded literature findings, experiment planning, code repair, measured-result interpretation, synthesis, and qualitative judgment.
- Checkpointed SQLite state, leased background jobs, retries, cancellation, fenced writes, and live trace events.
- Crossref/arXiv metadata, bounded public paper downloading, PDF/text/HTML/Markdown ingestion, and persistent hybrid retrieval with optional local semantic embeddings.
- Docker execution of generated Python with resource limits and validated measurements and SVG plots. Full, Limited, and Restricted deployment policies control models, budgets, and experiment permissions.
- An inspectable web workspace and a backend-free gallery containing three curated investigations, with portable ZIP exports and integrity-checked publication.
- Optional API bearer authentication, isolated reader/researcher workspaces, RS256 token verification, cost accounting, and operational telemetry.
- Automated provider, pipeline, storage, job, API, export, and frontend tests. Validation scope and commands are documented in the [README](../README.md#verification).

## Remaining work

- Systematic live evaluation of scientific validity, model-generated code, source selection, and qualitative assessments. Offline fixtures and finite synthetic demos do not establish broad reliability.
- OCR and more reliable multi-column, equation, theorem, and proof extraction; theorem-prover integration remains open.
- Retrieval-quality evaluation with provisioned semantic model weights.
- Broader Linux/Docker Desktop runtime and resource-isolation validation, plus support for controlled executor images with additional scientific dependencies. Native Windows containers need a separate policy.
- Browser token entry, saved-investigation history, OAuth login, live organizational OIDC integration, and dynamic tenant provisioning.
- Distributed scheduling, shared artifact storage, cross-node recovery, and distributed tracing validation. Current job storage is single-host SQLite.
- Live publisher/collector integration checks and a maintained process for updating model prices.

Historical development audits and benchmark percentages have been removed from the public documentation. The historical tool-selection dataset is retained as a diagnostic; it is not a performance claim about the current conjecture pipeline.
