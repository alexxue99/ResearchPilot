export function demoAssetUrl(slug, path) {
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug) || typeof path !== 'string' ||
      path.includes('\\') || path.includes(':') || path.split('/').some(part => !part || part === '.' || part === '..')) {
    throw new Error('Invalid demo asset path.');
  }
  return `/demos/${slug}/${path.split('/').map(encodeURIComponent).join('/')}`;
}

export function validateDemoState(value) {
  const arrays = ['plan', 'assumptions', 'constraints', 'sources', 'evidence', 'experiments_planned',
    'experiments_completed', 'unresolved_questions', 'conclusions', 'critique', 'artifacts', 'trace'];
  if (!value || typeof value.id !== 'string' || typeof value.question !== 'string' ||
      typeof value.report !== 'string' || typeof value.confidence !== 'number' ||
      !['draft', 'planned', 'needs_input', 'completed', 'failed', 'cancelled'].includes(value.status) ||
      arrays.some(key => !Array.isArray(value[key]))) {
    throw new Error('This demo snapshot is incomplete or unsupported.');
  }
  return value;
}

export function demoReportHref(slug, href) {
  if (!slug || typeof href !== 'string' || !/^(artifacts|experiments)\//.test(href)) return href;
  try { return demoAssetUrl(slug, href); } catch { return undefined; }
}
