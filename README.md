# ResearchPilot

ResearchPilot is a **research assistant for mathematical and machine learning conjectures.** The agent connects literature review, paper reading, and controlled experiments to synthesize a judgement on user provided conjectures. Users can then inspect the generated report, where every step in the process is detailed. Relevant passages from literature are highlighted, and all experimental details are provided.

The publc [website](https://research-pilot-psi.vercel.app/) is only a demo gallery showcase, so public functionality is limited. Details on the demo gallery are [below](#explore-the-demos). In order to use the agent, users must provide their own API key and set-up their own Docker environment. Check the [local setup guide](frontend/public/demos/local-setup.md).

![ResearchPilot interface](frontend/public/og.png)

## Explore the demos 

Browse the [demo gallery](https://research-pilot-psi.vercel.app/) to inspect three saved investigations, including their conjectures, sources, generated experiment Python code, plots, reports, and traces. The gallery is read-only and requires no installation, backend, or API key.

| Demo | Conjecture being tested | Saved report |
| --- | --- | --- |
| [Double descent](https://research-pilot-psi.vercel.app/?demo=double-descent) | Increasing random Fourier features produces an interpolation peak in noisy regression, and ridge regularization reduces that peak. | [Report](frontend/public/demos/double-descent/report.pdf) |
| [Spurious correlations](https://research-pilot-psi.vercel.app/?demo=spurious-correlations) | A predictive shortcut improves in-distribution accuracy but harms generalization when its correlation reverses. | [Report](frontend/public/demos/spurious-correlations/report.pdf) |
| [Overfitting under label noise](https://research-pilot-psi.vercel.app/?demo=overfitting-noise) | Increasing training-set size narrows the gap between noisy-label training accuracy and clean test accuracy. | [Report](frontend/public/demos/overfitting-noise/report.pdf) |

Below are two snapshots of the double descent demo.
![Experimental results from the double-descent demo, plotting test MSE against feature count for minimum-norm least squares and three ridge strengths](docs/images/demo-results.svg)

*Experimental results for the double descent demo.*

![Complete double-descent experiment pseudocode, showing data generation, random Fourier features, interpolation-threshold detection, least-squares and ridge fitting, test evaluation, and aggregation](docs/images/demo-pseudocode.jpg)

*Pseudocode for the double descent demo.*

## How investigations work

After the user enters a true/false conjecture and optionally attaches papers for context, the pipeline:

1. Interprets the statement and its assumptions.
2. Reasons about the conjecture, searches for corroborating sources, and retrieves additional scholarly literature.
3. Reviews paper passages and separates support, contradiction, qualifications, and related context.
4. Designs a finite numerical test, generates Python and visualization code, and executes it when Docker is configured.
5. Synthesizes the recorded evidence into a qualitative assessment and an inspectable report.

Missing papers, invalid model responses, code-generation failures, and execution failures remain visible in the trace. The code can be downloaded or modified by the user directly in the Experiments tab. If the user then reruns the code through the provided "rerun code" feature, the agent will automatically resynthesize the collected evidence and regenerate the report.

```mermaid
flowchart LR
    Gallery[Saved demo gallery] --> Snapshots[Static reports, code, plots and traces]
    Workspace[Local research workspace] --> API[FastAPI]
    API --> Queue[Leased SQLite jobs]
    Queue --> Agent[Conjecture pipeline]
    Agent --> Papers[Literature and paper passages]
    Agent --> Experiments[Generated Python in Docker]
    Agent --> State[Checkpoints, evidence and reports]
    State --> Export[Portable demo ZIP]
    Export --> Gallery
```

The workflow lives in [conjecture_pipeline.py](researchpilot/conjecture_pipeline.py). See the [workflow guide](docs/CONJECTURE_WORKFLOW.md) for stage contracts and the [security model](docs/SECURITY.md) for execution boundaries.

<!-- ## Save and publish a demo

Runs persist locally in `.researchpilot/`, which is ignored by Git. To preserve a result, use **Report → Save demo ZIP**, or:

```powershell
python -m researchpilot.cli list
python -m researchpilot.cli export-demo RESEARCH_ID --output demos/my-investigation.zip
python -m researchpilot.cli publish-demo demos/my-investigation.zip --slug my-investigation --title "My investigation" --summary "The conjecture and comparison being tested."
```

Demo export includes a typeset `report.pdf` and requires XeLaTeX with the TeX Gyre fonts on the backend host. Review the ZIP before publishing.

`publish-demo` verifies the manifest, typesets older snapshots that lack a PDF, and prepares gallery files under `frontend/public/demos/`. It does not deploy the website. Commit the curated ZIPs and prepared gallery assets; Vercel serves the saved PDFs without a backend or LaTeX installation.

Existing ZIP filenames and gallery slugs are protected from overwriting by default. Use `publish-demo --replace` to refresh an existing gallery entry. See the [demo guide](demos/README.md) for the snapshot format and workflow.

For Vercel, select `frontend` as the project root, use the Next.js preset, and set `NEXT_PUBLIC_RESEARCHPILOT_DEMO_ONLY=true` before building. Set `NEXT_PUBLIC_SITE_URL` to the public site URL. The curated gallery works without a deployed backend. -->

## Verification

Install test dependencies and run the offline suite:

```powershell
python -m pip install -e ".[api,literature,math,dev,identity,telemetry]"
python -m unittest discover -s tests -q
cd frontend
npm test
npm run lint
npm run typecheck
npm run build
```

Tests cover grounding, provider routing, paper ingestion, persistence, API/job behavior, generated experiment handling, portable exports, and frontend presentation. Docker runtime checks require explicit opt-in. The standalone `tests/test_katex_provider.py` tool makes a paid model request only when explicitly run in live mode; ordinary test discovery does not call it.