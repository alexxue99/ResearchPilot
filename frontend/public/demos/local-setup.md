# Run ResearchPilot locally

Download or clone the ResearchPilot repository, then open a terminal in its root.
Requires Python 3.11+, Node.js 22.13+, and Docker for experiment execution.
**Running model-guided investigations also requires your own API key backed
by an account with funding or available API credits. You pay for the model calls;
ResearchPilot does not provide an API key or cover those costs.** Viewing saved
demos requires no API key.

## Install and configure

### 1. Install the Python packages

The first command creates a project-specific Python environment in `.venv`;
the second activates it in the current PowerShell terminal. Create it once,
then activate it again whenever you open a new terminal for backend commands.

```powershell
python -m venv .venv
.venv/Scripts/Activate.ps1
python -m pip install -e ".[api,literature,science,math]"
Copy-Item .env.example .env.local
```

On macOS/Linux, activate with `source .venv/bin/activate` and copy with
`cp .env.example .env.local`.

### 2. Install and start Docker

On Windows, install [Docker Desktop using Docker's instructions](https://docs.docker.com/desktop/setup/install/windows-install/),
then open Docker Desktop and wait for the engine to start. Use Linux containers
(the WSL 2 backend supports them). Docker Desktop is the container runtime; you
still need to download a Python **image**, which provides the environment in
which experiment scripts run.

Check that the Docker client can reach the running engine:

```powershell
docker version
```

The output should include both Client and Server information. If it reports
that it cannot connect, start Docker Desktop before continuing.

### 3. Download and verify the Python image

From the repository root, with `.venv` activated and an internet connection, run:

```powershell
python -m researchpilot.cli images prepare --image python:3.13-slim
python -m researchpilot.cli images inspect --image python:3.13-slim
```

`prepare` downloads the official `python:3.13-slim` image from Docker Hub,
checks that it is a Linux image, and creates a workspace-owned alias. Its JSON
output includes an `alias` beginning with `researchpilot-executor:`. `inspect`
confirms that the downloaded image is available locally and reports its ID and OS.
ResearchPilot never downloads images automatically when running an experiment;
this preparation step must finish first.

### 4. Configure the backend

Edit the repository-root `.env.local` you copied in step 1:

```dotenv
OPENAI_API_KEY=your-api-key
RESEARCHPILOT_STRONG_MODEL=your-provider-model-name
RESEARCHPILOT_MODE=Full
RESEARCHPILOT_EXECUTOR=docker
RESEARCHPILOT_DOCKER_IMAGE=python:3.13-slim
```

Replace the API key and model placeholders with your own values. The image name
above uses the official tag you just downloaded. Alternatively, set
`RESEARCHPILOT_DOCKER_IMAGE` to the exact `alias` printed by `prepare` to use
that prepared image ID. Keep credentials in the backend configuration.

#### Choose a research mode

`RESEARCHPILOT_MODE` controls model selection, usage limits, and experiment
execution. The current accepted values are `Full`, `Limited`, and `Restricted`
(case-insensitive).

| Mode | Models | Experiments | Usage limits |
| --- | --- | --- | --- |
| `Full` (default) | Uses the strong model for every stage. | Enabled when Docker is configured; up to 300 seconds per execution. | No bounded daily-run, token, or cost quota; model calls can incur greater costs. |
| `Limited` | Uses the weak model for routine stages and the strong model for synthesis and judgment. | Enabled when Docker is configured; 60 seconds per execution by default. | Bounds daily runs, steps, papers, tokens, and estimated cost. |
| `Restricted` | Uses the weak model for every stage. | Execution and reruns are disabled; planning and code generation remain available. | Uses the same bounded defaults as Limited, with separate overrides. |

The read-only **demo gallery** is a separate frontend setting:
`NEXT_PUBLIC_RESEARCHPILOT_DEMO_ONLY=true` in `frontend/.env.local`. It displays
saved investigations without a backend or API key and does not run new research.
Set it to `false` and configure `NEXT_PUBLIC_RESEARCHPILOT_API` to enable the
local workspace.

For Full, set `RESEARCHPILOT_STRONG_MODEL` to your strong model's
provider name. Limited also needs `RESEARCHPILOT_WEAK_MODEL`; Restricted needs
only the weak model.

Restart the backend and any separate worker after changing `.env.local` so they
load the updated model settings.

Edit the repository-root `prices.json` to add or update the models you use under
`providers.openai`, using the exact model names from your configuration. Set
`input_per_million`, `output_per_million`, and, where applicable,
`cached_input_per_million` to the provider's rates in USD per million tokens, and
update `as_of` to the date you checked those rates (`YYYY-MM-DD`). In the root
`.env.local`, set `RESEARCHPILOT_PRICE_TABLE` to the full path to that file, for
example `RESEARCHPILOT_PRICE_TABLE=C:/path/to/researchpilot/prices.json`.
Limited and Restricted use these prices to enforce estimated-cost limits.

**Dependency limitation:** this image includes Python and its standard library,
but not NumPy, SciPy, Matplotlib, or SymPy. Installing the project's `science`
and `math` extras in `.venv` does not install them inside Docker. Scripts that
import those packages will fail with a missing-module error in the stock image;
check the generated script's imports before rerunning it. Experiment containers
have no network access, so they cannot download packages during execution.

The current image manager prepares official Python images only; it does not
build dependency-equipped images, and arbitrary custom image names are rejected
by the executor. The repository's root `Dockerfile` builds the API service,
not a supported experiment image. Preparing the stock image enables
standard-library experiments; additional scientific dependencies require
executor/image support beyond this setup guide.

## Start the application

```powershell
python -m uvicorn researchpilot.api:create_app --factory --host 127.0.0.1 --port 8000
```

In a second terminal:

```powershell
cd frontend
npm ci
Copy-Item .env.example .env.local
npm run dev
```

Visit http://localhost:3000 and choose **Local workspace**. Enter the demo's
conjecture and attach its method paper where relevant. Model calls incur costs
on your own API account. Seeds, package versions and model changes can affect
the outcome; a new run is not guaranteed to reproduce identical results.

## Save a result

Open **Report → Save demo ZIP**, or export from the repository root:

```powershell
python -m researchpilot.cli list
python -m researchpilot.cli export-demo RESEARCH_ID --output demos/my-result.zip
```

A downloaded demo ZIP can also be inspected without executing code. Read
`state.json`, `report.md`, and the experiment scripts and plots. It does not
restore a database or include a complete execution environment.
