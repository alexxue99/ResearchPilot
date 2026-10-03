export interface DemoEntry {
  slug: string; title: string; summary: string; question: string;
  status: string; model: string; created_at: string; exported_at: string; experiments: number;
  report_pdf?: string;
}

export function DemoGallery({demos, loading, error, onSelect}: {
  demos: DemoEntry[]; loading: boolean; error: string; onSelect: (slug: string) => void;
}) {
  return <section className="demo-gallery" aria-label="Demo gallery">
    <header className="gallery-intro"><span className="eyebrow">Research you can inspect</span>
      <h1>Follow the evidence.</h1>
      <p>Explore saved investigations from conjecture to sources, experiment code, measured results, and assessment.</p>
      <span className="gallery-note">Precomputed results · No API key required</span>
    </header>
    {loading ? <p role="status">Loading investigations…</p>
      : error ? <p role="alert">{error}</p>
      : demos.length === 0 ? <div className="gallery-empty"><h2>Demo investigations are coming soon</h2>
          <p>The gallery is ready. Curated results will appear here after they have been run and reviewed.</p>
          <a href="/demos/local-setup.md" download>Download the local setup guide</a></div>
      : <div className="demo-grid">{demos.map(demo => <article className="demo-card" key={demo.slug}>
          <div className="demo-card-meta"><span>Precomputed demo</span><span>{demo.status.replaceAll('_', ' ')}</span></div>
          <h2>{demo.title}</h2><p>{demo.summary}</p>
          <dl><div><dt>Run date</dt><dd>{demo.created_at.slice(0, 10)}</dd></div>
            <div><dt>Model</dt><dd>{demo.model}</dd></div>
            <div><dt>Completed experiments</dt><dd>{demo.experiments}</dd></div></dl>
          <button type="button" onClick={() => onSelect(demo.slug)}>Explore investigation <span aria-hidden="true">→</span></button>
        </article>)}</div>}
  </section>;
}
