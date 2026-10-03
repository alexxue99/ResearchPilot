r"""Standalone live planner/KaTeX test; importing this file makes no API calls.

Run from the repository root (frontend npm dependencies must be installed):
    .venv\Scripts\python.exe tests/test_katex_provider.py --case fourier
    .venv\Scripts\python.exe tests/test_katex_provider.py --case kaczmarz
    .venv\Scripts\python.exe tests/test_katex_provider.py --context planner-context.json
    .venv\Scripts\python.exe tests/test_katex_provider.py --response saved-report.json

--context accepts a JSON object with normalized_statement, objects, assumptions,
and optionally max_repetitions/max_experiment_seconds. --response accepts either
a bare planner response or a report previously saved by this script. Each live
run makes one fresh planner request, without running an investigation/experiment.
Exit codes: 0 = parse checks pass, 1 = parse/schema failure or no math produced,
2 = setup/provider failure. --strict-provider also fails on conversion repairs.
Parsing checks syntax, not mathematical correctness or completeness; the saved
original text and rendered HTML allow inspection of mixed plain-text notation.
Every completed run also saves a .preview.html beside the JSON report. Open it
in a browser to compare the provider output with the app rendering; its KaTeX
styles and fonts are embedded, so the preview works offline without a server.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from researchpilot import prompts
from researchpilot.agent.provider import OpenAIResponsesProvider, ProviderError
from researchpilot.conjecture_pipeline import structured_output_error
from researchpilot.deployment import DeploymentConfig
from researchpilot.environment import load_workspace_env

# Mirrors ConjecturePipeline.plan_experiments, including its strict output shape.
PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        **{key: {"type": "string"} for key in ("name", "parameter_ranges_json")},
        **{key: {"type": "array", "items": {"type": "string"}} for key in
           ("baselines", "metrics", "algorithm_steps", "additional_assumptions")},
        "seeds": {"type": "array", "items": {"type": "integer"}},
        "test_type": {"type": "string", "enum": ["falsifying", "illustrative", "not_meaningful"]},
    },
    "required": ["name", "baselines", "metrics", "seeds", "algorithm_steps",
                 "parameter_ranges_json", "additional_assumptions", "test_type"],
}

CASES = {
    "fourier": {
        "normalized_statement": (
            "For noisy synthetic regression using Fourier features and a minimum-norm "
            "least-squares fit, test mean squared error exhibits double descent as "
            "the number of features crosses the training sample count; ridge "
            "regularization reduces the interpolation peak."
        ),
        "objects": ["Fourier feature design matrix", "singular-value decomposition",
                    "minimum-norm pseudoinverse estimator", "ridge estimator", "test MSE"],
        "assumptions": ["Independent Gaussian observation noise", "Fixed independent test set",
                        "Compare feature counts below, near, and above interpolation",
                        "Use an explicit relative cutoff for pseudoinverse singular values"],
    },
    "kaczmarz": {
        "normalized_statement": (
            "For a consistent full-column-rank linear system, randomized Kaczmarz "
            "with row probabilities proportional to squared row norms has expected "
            "squared error bounded by the initial squared error times "
            "(1 - sigma_min(A)^2 / ||A||_F^2)^k after k iterations."
        ),
        "objects": ["Matrix A", "row vectors", "exact solution", "randomized row updates",
                    "smallest singular value", "expected squared error"],
        "assumptions": ["Consistent system", "Full column rank", "Nonzero rows",
                        "Compare flat and decaying singular-value profiles at equal Frobenius norm"],
    },
}

# Execute in frontend so Node resolves the same installed rendering dependencies.
# The raw route changes delimiters only: it must not repair provider syntax.
RENDER_AUDIT_JS = r"""
import fs from 'node:fs';
import katex from 'katex';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import {unified} from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import {normalizeExperimentMath, flattenExperimentSettings} from './app/experiment-math.mjs';

const response = JSON.parse(fs.readFileSync(0, 'utf8'));
const fields = [];
function collect(value, path) {
  if (typeof value === 'string') fields.push({path, original: value});
  else if (Array.isArray(value)) value.forEach((item, i) => collect(item, `${path}[${i}]`));
}
for (const key of ['name', 'baselines', 'metrics', 'algorithm_steps', 'additional_assumptions']) {
  collect(response[key], key);
}
let settingsError = null;
try {
  const settings = JSON.parse(response.parameter_ranges_json);
  for (const item of flattenExperimentSettings(settings)) {
    fields.push({path: `settings.${item.label}`, original: item.value});
  }
} catch (error) { settingsError = error.message; }

function delimiterOnly(text) {
  return text.split(/(`{3,}[\s\S]*?`{3,}|`[^`\n]*`)/g).map((part, i) => i % 2 ? part : part
    .replace(/\\\[([\s\S]*?)\\\]/g, (_, value) => `\n\n$$\n${value.trim()}\n$$\n\n`)
    .replace(/\\\(([^\n]*?)\\\)/g, (_, value) => `$${value}$`)).join('');
}
function parseMath(text) {
  const formulas = [];
  const tree = unified().use(remarkParse).use(remarkGfm).use(remarkMath).parse(text);
  function visit(node) {
    if (node.type === 'math' || node.type === 'inlineMath') {
      let error = null;
      try { katex.renderToString(node.value, {throwOnError: true, displayMode: node.type === 'math'}); }
      catch (failure) { error = failure.message; }
      formulas.push({tex: node.value, error});
    }
    node.children?.forEach(visit);
  }
  visit(tree);
  return formulas;
}
for (const field of fields) {
  const raw = delimiterOnly(field.original);
  field.normalized = normalizeExperimentMath(field.original, {inferPlainMath: true});
  field.provider_math = parseMath(raw);
  field.rendered_math = parseMath(field.normalized);
  field.conversion_changed_math = raw !== field.normalized;
  function render(text) { return renderToStaticMarkup(React.createElement(ReactMarkdown, {
    remarkPlugins: [remarkGfm, remarkMath], rehypePlugins: [rehypeKatex],
    components: {p: ({children}) => React.createElement(React.Fragment, null, children)},
  }, text)); }
  field.provider_html = render(raw);
  field.html = render(field.normalized);
  field.render_error = /class="[^"]*katex-error/.test(field.html);
}
console.log(JSON.stringify({fields, settings_error: settingsError}));
"""


def audit_response(response: dict) -> dict:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required for the real frontend rendering check")
    completed = subprocess.run(
        [node, "--input-type=module", "-e", RENDER_AUDIT_JS], cwd=ROOT / "frontend",
        input=json.dumps(response), text=True, encoding="utf-8", capture_output=True, timeout=60,
    )
    if completed.returncode:
        raise RuntimeError("Frontend audit failed (run npm install in frontend): " + completed.stderr[-2000:])
    return json.loads(completed.stdout)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_preview(report: dict, output: Path) -> Path:
    """Package the actual renderer output with local KaTeX CSS/fonts for inspection."""
    katex_dist = ROOT / "frontend" / "node_modules" / "katex" / "dist"
    css = (katex_dist / "katex.min.css").read_text(encoding="utf-8")

    def embed_font(match):
        relative = match.group(1).strip("\"'")
        font = (katex_dist / relative).resolve()
        if not font.is_relative_to(katex_dist.resolve()) or font.suffix not in (".woff2", ".woff", ".ttf"):
            raise ValueError("Unexpected asset in installed KaTeX stylesheet")
        mime = {".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf"}[font.suffix]
        encoded = base64.b64encode(font.read_bytes()).decode("ascii")
        return f'url("data:{mime};base64,{encoded}")'

    css = re.sub(r"url\(([^)]+)\)", embed_font, css)
    cards = []
    for field in report["audit"]["fields"]:
        errors = []
        for route, label in (("provider_math", "Provider"), ("rendered_math", "App")):
            errors.extend(f"{label}: {formula['error']}" for formula in field[route] if formula["error"])
        status = "Parse error" if errors or field["render_error"] else (
            "Conversion changed text" if field["conversion_changed_math"] else "Unchanged")
        error_html = "".join(f'<pre class="error">{escape(error)}</pre>' for error in errors)
        cards.append(f'''<article>
<header><h2>{escape(field["path"])}</h2><span>{status}</span></header>
{error_html}<div class="comparison">
<section><h3>Provider output (before conversion)</h3><div class="rendered">{field["provider_html"]}</div></section>
<section><h3>App rendering (after conversion)</h3><div class="rendered">{field["html"]}</div></section>
</div><details><summary>Original text and normalized text</summary>
<h3>Original</h3><pre>{escape(field["original"])}</pre>
<h3>Normalized</h3><pre>{escape(field["normalized"])}</pre></details></article>''')
    general_errors = "".join(f'<pre class="error">{escape(error)}</pre>' for error in
                             (report["schema_error"], report["audit"]["settings_error"]) if error)
    summary = report["summary"]
    preview = output.with_name(output.stem + ".preview.html")
    preview.write_text(f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; font-src data:; base-uri 'none'">
<title>KaTeX provider test preview</title><style>{css}</style><style>
body{{margin:0;background:#f3f5f8;color:#172235;font:16px/1.6 system-ui,sans-serif}}
main{{max-width:1200px;margin:auto;padding:32px 24px}}h1{{margin:0}}h2{{font-size:18px;margin:0}}
h3{{font-size:14px;color:#526079}}article{{background:white;border:1px solid #dbe1ea;border-radius:12px;padding:20px;margin:18px 0}}
header{{display:flex;gap:16px;align-items:center;justify-content:space-between}}header span{{font-size:13px}}
.comparison{{display:grid;grid-template-columns:1fr 1fr;gap:24px}}section{{min-width:0}}
.rendered{{overflow:auto;padding:16px 8px;min-height:48px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f8;padding:12px;border-radius:6px}}
.error{{background:#fff0f0;color:#a32020}}summary{{cursor:pointer;color:#315c9e}}code{{overflow-wrap:anywhere}}
@media(max-width:760px){{.comparison{{grid-template-columns:1fr}}header{{align-items:flex-start;flex-direction:column}}}}
</style></head><body><main>
<h1>KaTeX provider test: {"PASS" if report["passed"] else "FAIL"}</h1>
<p>{summary["rendered_formulas"]} rendered formulas · {summary["provider_parse_errors"]} provider parse errors ·
{summary["rendered_parse_errors"]} app parse errors · {summary["converted_fields"]} converted fields</p>
<p>Compare the original provider output with the experiment page renderer. Expand each card to inspect the LaTeX source.
Passing these checks establishes parseability; inspect the formulas for unintended notation or missing terms.</p>
{general_errors}{"".join(cards) or "<p>No rendered fields were available.</p>"}
</main></body></html>''', encoding="utf-8")
    return preview


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--case", choices=CASES, default="fourier")
    source.add_argument("--context", type=Path, help="Planner input JSON")
    source.add_argument("--response", type=Path, help="Replay a saved response/report without an API call")
    parser.add_argument("--model", help="Override the configured planner model")
    parser.add_argument("--max-output-tokens", type=int, default=5000)
    parser.add_argument("--output", type=Path, help="Report JSON path (default: artifacts/katex-provider/<timestamp>.json)")
    parser.add_argument("--strict-provider", action="store_true", help="Fail if frontend conversion repairs/inference were needed")
    args = parser.parse_args(argv)
    if args.max_output_tokens < 1:
        parser.error("--max-output-tokens must be positive")
    report = {}
    try:
        if args.response:
            saved = read_json(args.response)
            if not isinstance(saved, dict):
                raise ValueError("Saved response/report must be a JSON object")
            response = saved.get("response", saved)
            report.update(source="replay", replay_path=str(args.response.resolve()))
        else:
            load_workspace_env(ROOT)
            config = DeploymentConfig.from_env()
            context = read_json(args.context) if args.context else dict(CASES[args.case])
            if not isinstance(context, dict) or not isinstance(context.get("normalized_statement"), str):
                raise ValueError("Context must be an object containing a string normalized_statement")
            for key in ("objects", "assumptions"):
                if not isinstance(context.get(key), list) or any(not isinstance(x, str) for x in context[key]):
                    raise ValueError(f"Context {key} must be an array of strings")
            context.setdefault("max_repetitions", config.experiment_seed_limit)
            context.setdefault("max_experiment_seconds", config.experiment_timeout_seconds)
            messages = [
                {"role": "developer", "content": prompts.EXPERIMENT_PLANNING + "\n\n" +
                 prompts.output_instructions("experiment_planning", PLAN_SCHEMA)},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ]
            provider = OpenAIResponsesProvider.from_env(args.model or config.routine_model,
                                                       max_output_tokens=args.max_output_tokens)
            provider.planning_timeout = config.planning_timeout_seconds
            # A diagnostic run should not silently repeat a paid request.
            provider.max_retries = 0
            print(f"Querying {provider.name} with the experiment-planning prompt...", flush=True)
            report.update(source="live", model=provider.model, messages=messages, schema=PLAN_SCHEMA)
            response = provider.generate_plan(messages, PLAN_SCHEMA)
            report.update(usage=provider.last_usage, cost_usd=provider.last_cost_usd,
                          price_known=provider.price_known)
        report["response"] = response
        report["schema_error"] = structured_output_error(response, PLAN_SCHEMA)
        if report["schema_error"]:
            audit = {"fields": [], "settings_error": None}
        else:
            audit = audit_response(response)
        report["audit"] = audit
        fields = audit["fields"]
        raw_errors = sum(bool(f["error"]) for item in fields for f in item["provider_math"])
        rendered_errors = sum(bool(f["error"]) for item in fields for f in item["rendered_math"])
        rendered_count = sum(len(item["rendered_math"]) for item in fields)
        changes = sum(item["conversion_changed_math"] for item in fields)
        report["summary"] = dict(provider_parse_errors=raw_errors, rendered_parse_errors=rendered_errors,
                                 rendered_formulas=rendered_count, converted_fields=changes)
        failed = bool(report["schema_error"] or audit["settings_error"] or raw_errors or rendered_errors
                      or not rendered_count or any(item["render_error"] for item in fields)
                      or (args.strict_provider and changes))
        report["passed"] = not failed
        output = args.output or ROOT / "artifacts" / "katex-provider" / (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
        output.parent.mkdir(parents=True, exist_ok=True)
        preview = write_preview(report, output)
        report["preview_path"] = str(preview.resolve())
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"{'FAIL' if failed else 'PASS'}: {rendered_count} rendered formulas; "
              f"{raw_errors} provider parse errors; {rendered_errors} rendered parse errors; "
              f"{changes} fields changed by conversion.")
        if report["schema_error"] or audit["settings_error"]:
            print(report["schema_error"] or audit["settings_error"])
        if not rendered_count:
            print("No renderable math was found; this does not count as a successful KaTeX test.")
        for item in fields:
            for route in ("provider_math", "rendered_math"):
                for formula in item[route]:
                    if formula["error"]:
                        print(f"  {item['path']} ({route}): {formula['error']}")
        print(f"Report: {output.resolve()}")
        print(f"Preview: {preview.resolve()}")
        return int(failed)
    except (ProviderError, ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"KaTeX test could not complete: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
