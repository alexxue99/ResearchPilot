# Curated demo snapshots

Keep selected investigation ZIPs in this directory. It is outside the ignored
`.researchpilot` runtime workspace, so demo snapshots can be committed and survive
runtime cleanup.

In the local web app, finish an investigation, open **Report**, and click
**Save demo ZIP**. Move the downloaded ZIP here with a descriptive filename.
This download works without a LaTeX installation.

Alternatively, export a saved run from the repository root:

```powershell
python -m researchpilot.cli list
python -m researchpilot.cli export-demo RESEARCH_ID --output demos/double-descent.zip
```

To run from the CLI and export immediately afterward:

```powershell
python -m researchpilot.cli research "Your precisely scoped conjecture" --save-demo demos/double-descent.zip
```

Use the local setup in the main README and configure Docker for generated code
execution before running. A failed export does not discard the locally saved
investigation; retry `export-demo` after correcting the error. Existing ZIPs are
never overwritten: use a new filename for another version.

Each ZIP includes:

- `manifest.json`: schema version, run/model/status/date metadata and file SHA-256 hashes.
- `state.json`: sources, recorded evidence, experiment designs/results, model usage and assessment.
- `report.md` and `trace.json`: readable report and investigation trace.
- `experiments/`: generated experiment and visualization scripts.
- `artifacts/`: referenced measurements, manifests and plots, using portable relative paths.

Missing or out-of-workspace artifacts and active jobs cause export to fail rather
than silently produce an incomplete snapshot. Environment files, databases,
unrelated investigations and unreferenced caches are excluded. Paper PDFs and ingestion metadata explicitly referenced as artifacts can be included. Secret fields
and configured credential values are redacted; review research text and artifacts
for private information before committing or publishing.

## Add a snapshot to the website gallery

After reviewing a saved ZIP, run from the repository root:

```powershell
python -m researchpilot.cli publish-demo demos/double-descent.zip --slug double-descent --title "Double descent" --summary "How test error changes as a random-feature model grows."
```

This verifies the file hashes, prepares static files in
`frontend/public/demos/double-descent/`, and adds a card to `catalog.json`.
It prepares local website assets; it does not deploy the website. Existing demo
slugs are not overwritten. Use a new slug when publishing another version.

The gallery opens snapshots in the existing investigation tabs, with read-only
experiment code, static plots, a Markdown report and trace. A direct link such as
`/?demo=double-descent` opens the selected demo. Visitors can download its original
ZIP or the local setup guide. The repository includes three recorded investigations: double descent, spurious
correlations, and overfitting under label noise.

For a public Vercel deployment, set `NEXT_PUBLIC_RESEARCHPILOT_DEMO_ONLY=true`
before building. The gallery then makes no backend requests and hides the local
workspace. When no `NEXT_PUBLIC_RESEARCHPILOT_API` is configured, it defaults to
gallery-only mode. For local investigation runs, use the frontend environment
example with a local API and `NEXT_PUBLIC_RESEARCHPILOT_DEMO_ONLY=false`.
Commit the ZIP and prepared static assets before deploying the frontend.

Unzip to inspect results without running the backend. Snapshots retain failed or
inconclusive outcomes as well as successful ones; check the recorded status before
selecting a reviewer demo. Export preserves results but does not restore a run
into the local database. Scripts may depend on their
original scientific packages and generated file layout; the ZIP is a snapshot,
not a standalone execution environment.
