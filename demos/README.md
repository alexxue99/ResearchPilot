# Curated demo snapshots

Keep selected investigation ZIPs in this directory. It is outside the ignored
`.researchpilot` runtime workspace, so demo snapshots can be committed and survive
runtime cleanup.

In the local web app, finish an investigation, open **Report**, and click
**Save demo ZIP**. Move the downloaded ZIP here with a descriptive filename.
Demo export automatically typesets `report.pdf` using XeLaTeX. Install XeLaTeX and the TeX Gyre fonts on the backend host, or use the supplied backend Docker image. If typesetting fails, the export reports the error and preserves the local investigation.

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
- `report.pdf`: precomputed typeset report for the gallery and offline reading.
- `report.md` and `trace.json`: report source and investigation trace.
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
It prepares local website assets; it does not deploy the website. New snapshots
reuse their included PDF. Older Markdown-only ZIPs are automatically typeset
before publication, and the downloadable gallery ZIP gets the PDF and an updated
manifest. Those older source ZIPs remain unchanged. XeLaTeX is only needed on
the publishing machine when the input lacks a PDF; Vercel serves the saved files.

Existing demo slugs are protected by default. To refresh a published snapshot,
repeat the command with `--replace`. Validation and PDF generation happen before
the old gallery files are replaced. Use a new slug to keep both versions.

The gallery opens snapshots in the existing investigation tabs, with read-only
experiment code, static plots, a PDF report and trace. Markdown remains available
for download and as a fallback for older gallery entries without PDFs. A direct link such as
`/?demo=double-descent` opens the selected demo. Visitors can download its portable
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
