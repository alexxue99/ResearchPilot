import assert from 'node:assert/strict';
import {readFile, writeFile} from 'node:fs/promises';
import {ResearchClient} from '../frontend/app/api-client.mjs';

const base = process.argv[2];
const writer = new ResearchClient(base, 'alice-write');
const reader = new ResearchClient(base, 'alice-read');
const outsider = new ResearchClient(base, 'bob-write');
const anonymous = new ResearchClient(base);
const timeout = AbortSignal.timeout(30_000);
const post = body => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), signal: timeout});
try {
  assert.equal((await reader.json('/session')).role, 'reader');
  await assert.rejects(anonymous.json('/research'), {status: 401});
  await assert.rejects(reader.json('/research', post({question: 'Compare PCA reconstruction error.'})), {status: 403});
  const state = await writer.json('/research', post({question: 'Compare PCA reconstruction error with different ranks.'}));
  const path = `/research/${state.id}`;
  await writer.json(`${path}/papers`, post({filename: 'note.md', title: 'Private PCA note',
    content_base64: Buffer.from('# Reconstruction\nPCA reconstruction error decreases as retained rank increases.').toString('base64')}));
  const hits = await reader.json(`${path}/retrieval?q=reconstruction`);
  assert.ok(hits.length > 0);
  await assert.rejects(outsider.json(`${path}/retrieval?q=reconstruction`), {status: 404});
  await assert.rejects(reader.json(`${path}/continue`, post({})), {status: 403});
  await writer.json(`${path}/continue`, post({}));
  let terminal = false; let traces = 0;
  await reader.stream(`${path}/events`, event => {
    if (event.event === 'trace') traces++;
    if (event.event === 'state') {
      const update = JSON.parse(event.data);
      if (['completed', 'failed', 'needs_input'].includes(update.status)) {
        assert.equal(update.status, 'completed'); terminal = true; return false;
      }
    }
  }, timeout);
  assert.ok(terminal, 'stream must deliver terminal completion');
  assert.ok(traces > 0, 'stream must deliver trace events');
  const completed = await reader.json(path);
  assert.equal(completed.experiments_completed.length, 0, 'unsupported experiment must not be reported as executed');
  assert.ok(completed.unresolved_questions.length > 0, 'unsupported computation must disclose uncertainty');
  assert.match(completed.report, /ResearchPilot assessment:/);
  // Modify only this test's temporary operator-owned identity file.
  const identitiesPath = process.argv[3];
  const identities = JSON.parse(await readFile(identitiesPath, 'utf8'));
  let revoked = false; let denial = false;
  await reader.stream(`${path}/events`, async event => {
    if (event.event === 'state' && !revoked) {
      await writeFile(identitiesPath, JSON.stringify(identities.filter(item => item.role !== 'reader')));
      revoked = true;
    } else if (event.event === 'error') {
      assert.equal(JSON.parse(event.data).message, 'stream authorization ended'); denial = true;
    } else if (revoked) {
      assert.fail('private event delivered after revocation');
    }
  }, timeout);
  assert.ok(revoked && denial, 'open stream must observe credential revocation');
  await assert.rejects(reader.json(path), {status: 401});
  reader.close();
  await assert.rejects(reader.json(path), {name: 'AbortError'});
  console.log('Live client/API: identity, RBAC, upload/retrieval, SSE, code proposal, tenant isolation, sign-out passed.');
} finally {
  for (const client of [writer, reader, outsider, anonymous]) client.close();
}
