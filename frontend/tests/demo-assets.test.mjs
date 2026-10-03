import assert from 'node:assert/strict';
import test from 'node:test';
import { demoAssetUrl, demoReportHref, validateDemoState } from '../app/demo-assets.mjs';

test('static demo assets resolve on the frontend origin', () => {
  assert.equal(demoAssetUrl('double-descent', 'artifacts/run/plot 1.svg'), '/demos/double-descent/artifacts/run/plot%201.svg');
  for (const path of ['../secret', '/absolute', 'https://other/plot.svg', 'a\\b', 'a/../b', '']) {
    assert.throws(() => demoAssetUrl('double-descent', path));
  }
  assert.throws(() => demoAssetUrl('../demo', 'state.json'));
  assert.equal(demoReportHref('double-descent', 'artifacts/plot.svg'), '/demos/double-descent/artifacts/plot.svg');
  assert.equal(demoReportHref('double-descent', 'https://arxiv.org/abs/1908.05355'), 'https://arxiv.org/abs/1908.05355');
  assert.equal(demoReportHref('double-descent', 'artifacts/../secret'), undefined);
});

test('snapshots must have required arrays and a nonrunning status', () => {
  const snapshot = {id: 'test', question: 'Question', report: 'Report', confidence: 0.5, status: 'completed'};
  for (const key of ['plan', 'assumptions', 'constraints', 'sources', 'evidence', 'experiments_planned', 'experiments_completed', 'unresolved_questions', 'conclusions', 'critique', 'artifacts', 'trace']) snapshot[key] = [];
  assert.equal(validateDemoState(snapshot), snapshot);
  assert.throws(() => validateDemoState({...snapshot, status: 'running'}));
  assert.throws(() => validateDemoState({...snapshot, artifacts: null}));
});
