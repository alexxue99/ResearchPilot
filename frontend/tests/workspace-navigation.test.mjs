import test from 'node:test';
import assert from 'node:assert/strict';
import { workspaceLocation, rememberInvestigation, recentInvestigationId } from '../app/workspace-navigation.mjs';

const id = 'ee45e59e-2696-4d39-a794-856594e7da8f';
const other = '4ffca2f9-4f0c-4f0a-8cf0-145b14bec17b';
const api = 'http://localhost:8000';
const storage = () => {
  const values = new Map();
  return {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value)};
};

test('Back, Forward, and reload resolve live investigation URLs independently of demos', () => {
  assert.deepEqual(workspaceLocation(`?research=${id}`), {demo: null, research: id});
  assert.deepEqual(workspaceLocation(''), {demo: null, research: null});
  assert.deepEqual(workspaceLocation(`?demo=example&research=${id}`), {demo: 'example', research: null});
  assert.deepEqual(workspaceLocation('?research=invalid'), {demo: null, research: null});
});

test('returning from gallery or demo restores the last local investigation without requesting history', async () => {
  const saved = storage();
  rememberInvestigation(saved, api, id);
  rememberInvestigation(saved, api, 'demo-id');
  const client = {json: async () => { throw new Error('History must not replace the selected investigation'); }};
  assert.equal(await recentInvestigationId(client, saved, api), id);
});

test('a workspace with no remembered investigation recovers the newest saved server result', async () => {
  const client = {json: async path => {
    assert.equal(path, '/research');
    return [{id: 'invalid'}, {id}, {id: other}];
  }};
  assert.equal(await recentInvestigationId(client, storage(), api), id);
});

test('remembered IDs are scoped to the backend and history works when browser storage is disabled', async () => {
  const saved = storage();
  rememberInvestigation(saved, 'http://localhost:9000', other);
  const client = {json: async () => [{id}]};
  assert.equal(await recentInvestigationId(client, saved, api), id);
  const disabled = {getItem: () => { throw new Error('Disabled'); }, setItem: () => { throw new Error('Disabled'); }};
  rememberInvestigation(disabled, api, id);
  assert.equal(await recentInvestigationId(client, disabled, api), id);
});

test('empty history leaves a workspace available for a new investigation', async () => {
  assert.equal(await recentInvestigationId({json: async () => []}, storage(), api), null);
});
