import assert from 'node:assert/strict';
import test from 'node:test';
import {experimentStatus} from '../app/experiment-status.mjs';

test('a falsifying design without measurements is not an outcome', () => {
  assert.equal(experimentStatus({test_type: 'falsifying'}), 'Not run');
  assert.equal(experimentStatus({test_type: 'falsifying', execution_status: 'timeout'}), 'Timed out · no results');
  assert.equal(experimentStatus({execution_status: 'skipped'}), 'Not run · execution skipped');
  assert.equal(experimentStatus({execution_status: 'failed'}), 'Failed · no results');
});

test('a recorded result determines the execution status', () => {
  assert.equal(experimentStatus({execution_status: 'timeout'}, {status: 'completed'}), 'Completed');
});
