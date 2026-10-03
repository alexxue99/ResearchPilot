export function experimentStatus(design, result) {
  const status = result?.status ?? design.execution_status ?? 'planned';
  const labels = {planned: 'Not run', skipped: 'Not run · execution skipped',
    timeout: 'Timed out · no results', failed: 'Failed · no results',
    unavailable: 'Not run · executor unavailable', completed: 'Completed'};
  return labels[status] ?? status;
}
