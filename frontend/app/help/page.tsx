import type { Metadata } from 'next';
import Link from 'next/link';

export const metadata: Metadata = {
  title: 'Help — ResearchPilot',
  description: 'Learn how ResearchPilot investigates mathematical conjectures and how to inspect its sources, experiments, and reports.',
};

const sections = [
  ['about', 'About the project'],
  ['getting-started', 'Getting started'],
  ['workflow', 'How it works'],
  ['workspace', 'Workspace guide'],
  ['results', 'Understanding results'],
  ['questions', 'Common questions'],
] as const;

export default function HelpPage() {
  return <div className="workspace-shell">
    <aside className="rail">
      <Link className="brand" href="/"><span className="brand-mark">R</span><span>ResearchPilot</span></Link>
      <nav aria-label="Help sections">
        <Link className="nav-item" href="/"><span className="nav-dot" />Back to workspace</Link>
        <Link className="nav-item active" href="/help" aria-current="page"><span className="nav-dot" />Help</Link>
        {sections.map(([id, label]) => <a className="nav-item" href={`#${id}`} key={id}>{label}</a>)}
      </nav>
      <div className="rail-bottom rail-meta"><strong>Research you can inspect</strong><span>From conjecture to evidence.</span></div>
    </aside>

    <main className="main-pane help-page" id="top">
      <header className="gallery-intro">
        <span className="eyebrow">ResearchPilot guide</span>
        <h1>Help</h1>
        <p>Understand the project, start an investigation, and follow the evidence behind its conclusions.</p>
      </header>

      <section className="help-section" id="about">
        <span className="eyebrow">About the project</span>
        <h2>An inspectable research assistant</h2>
        <p>ResearchPilot helps investigate mathematical conjectures by connecting paper reading, literature search, experiment design, and evidence synthesis in one workspace. You can inspect the sources, generated Python, measured results, and investigation trace behind a report.</p>
        <p>It is an investigation tool, not a theorem prover. Numerical experiments help give you intuition, but they only test finite cases. A promising experimental result does not establish a universal mathematical claim.</p>
      </section>

      <section className="help-section" id="getting-started">
        <h2>Getting started</h2>
        <div className="help-grid">
          <article className="help-card"><h3>Explore a saved demo</h3><p>Open the <Link href="/">Demo gallery</Link> and choose an investigation. Browse its sources, experiment code, results, report, and trace. Demos are precomputed, read only, and require no API key. Download a snapshot to keep a portable copy.</p></article>
          <article className="help-card"><h3>Run your own investigation</h3><p>In a deployment with a connected backend, choose <strong>Local workspace</strong>. Enter a specific conjecture, optionally attach an arXiv link or PDF before starting, then start the investigation. If it requests clarification, answer the displayed questions and submit your response.</p></article>
        </div>
        <p>A useful conjecture names the method, comparison, and conditions you want to test. For example: “Randomized Kaczmarz becomes slower when singular values decay harmonically rather than remaining approximately flat.” Attach a paper when its definitions matter to the comparison.</p>
        <a className="help-setup-link" href="/demos/local-setup.md" download>Download the local setup guide <span aria-hidden="true">↓</span></a>
      </section>

      <section className="help-section" id="workflow">
        <h2>How an investigation works</h2>
        <ol className="help-steps">
          <li><strong>Interpret the claim.</strong> Identify the question, assumptions, and relevant methods, using attached paper passages where available.</li>
          <li><strong>Reason and search.</strong> Develop initial reasoning, search for corroborating sources, and find additional literature through Crossref and arXiv.</li>
          <li><strong>Extract evidence.</strong> Review selected papers and connect findings to retrieved source passages.</li>
          <li><strong>Plan and execute experiments.</strong> Specify baselines, metrics, parameters, and seeds; generate inspectable Python and run bounded numerical tests when execution is enabled.</li>
          <li><strong>Synthesize and report.</strong> Assess supporting and conflicting evidence, explain uncertainty and limitations, and produce a report with an investigation trace.</li>
        </ol>
        <p>The investigation map shows each stage’s progress. A run can retain useful sources and plans even when a later stage cannot finish.</p>
      </section>

      <section className="help-section" id="workspace">
        <h2>Find your way around the workspace</h2>
        <dl className="help-tabs">
          <div><dt>Investigation</dt><dd>The conjecture, assumptions, stage progress, and completed results at a glance.</dd></div>
          <div><dt>Sources</dt><dd>Retrieved papers and source details. Inspect the passages behind a finding and open available public paper links.</dd></div>
          <div><dt>Experiments</dt><dd>Experiment designs, settings, generated code, execution status, and recorded measurements. Check that the test matches your claim.</dd></div>
          <div><dt>Report</dt><dd>The evidence synthesis, qualitative assessment, and limitations. Available exports include a typeset PDF and a demo ZIP; local PDF generation requires the configured LaTeX tooling.</dd></div>
          <div><dt>Trace</dt><dd>The recorded actions and their status. Use it to understand what ran, where an investigation stopped, and what needs attention.</dd></div>
        </dl>
      </section>

      <section className="help-section" id="results">
        <h2>Understanding the results</h2>
        <p>Read the assessment together with its cited passages, experiment settings, and limitations. A paper that defines an algorithm may provide context without supporting the conjecture. A planned experiment or generated script is not a measured result; measurements require a completed execution.</p>
        <p>The report gives a qualitative judgment of the recorded evidence and its scope, rather than a probability score. Search coverage and PDF parsing are incomplete, and generated experiments need human scrutiny before their results are used as research claims.</p>
        <div className="help-callout"><h3>Check the scope before drawing a conclusion</h3><p>Look for the tested parameters, assumptions, baselines, and random seeds. Consider whether the evidence supports only those cases, whether sources disagree, and which questions remain unresolved.</p></div>
      </section>

      <section className="help-section" id="questions">
        <h2>Common questions</h2>
        <details className="help-faq"><summary>Why can I only view demos?</summary><p>The public gallery can run without a backend. Creating investigations requires a local or connected deployment with a configured model provider. Follow the local setup guide to enable your own workspace.</p></details>
        <details className="help-faq"><summary>Why did an experiment not run?</summary><p>Generated experiments require an enabled Docker executor and a compatible local image with the required dependencies. Restricted mode disables execution; Full and Limited modes permit it when configured. Inspect the experiment status and Trace for the recorded reason. Code and plans can remain available for review even without execution.</p></details>
        <details className="help-faq"><summary>What do the deployment modes mean?</summary><p>Full uses the configured strong model throughout and permits experiments up to 300 seconds. Limited uses a weaker model for routine stages and a stronger model for synthesis and judgment, with bounded usage and shorter experiments. Restricted uses the weak model throughout and disables experiment execution. All modes permit at most five random seeds per design.</p></details>
        <details className="help-faq"><summary>What if a run stops or reaches a limit?</summary><p>Read the notice and Trace, then inspect the report’s unresolved questions and limitations. A model usage limit can produce a partial report from evidence collected so far. If the workspace asks for input, provide the missing details to continue. In a live investigation, use Stop investigation to request cancellation.</p></details>
        <details className="help-faq"><summary>How do I save or reproduce an investigation?</summary><p>Live investigations persist in the backend workspace. Use Report → Save demo ZIP for a portable snapshot of the state, report, trace, scripts, and referenced artifacts. Demo investigations offer a Download snapshot. To reproduce experiments, review the code, dependencies, settings, seeds, and model configuration; results may vary when these change.</p></details>
      </section>

      <footer className="help-footer"><Link href="/">Back to ResearchPilot <span aria-hidden="true">→</span></Link></footer>
    </main>
  </div>;
}
