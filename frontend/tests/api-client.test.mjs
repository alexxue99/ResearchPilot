import test from 'node:test';
import assert from 'node:assert/strict';
import {ResearchClient, ApiError, parseEvent} from '../app/api-client.mjs';

test('default fetch transport keeps its required global receiver', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async function () {
    assert.equal(this, globalThis);
    return Response.json({ok: true});
  };
  try {
    const client = new ResearchClient('http://localhost:8000');
    assert.deepEqual(await client.json('/health'), {ok: true});
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('credentials travel only in headers and redirects are rejected', async () => {
  const client = new ResearchClient('https://example.test', 'secret', async (url, options) => {
    assert.equal(url, 'https://example.test/research');
    assert.equal(options.headers.get('Authorization'), 'Bearer secret');
    assert.equal(options.redirect, 'error'); assert.equal(options.credentials, 'omit');
    return Response.json([]);
  });
  assert.deepEqual(await client.json('/research'), []);
});

test('sign-out invalidates in-flight responses and later calls', async () => {
  let resolve;
  const client = new ResearchClient('http://localhost:8000', 'secret', () => new Promise(r => resolve = r));
  const pending = client.json('/research');
  client.close(); resolve(Response.json({private: true}));
  await assert.rejects(pending, {name: 'AbortError'});
  await assert.rejects(client.json('/research'), {name: 'AbortError'});
  assert.equal(client.token, '');
});

test('401 and 403 propagate meaningful errors', async () => {
  for (const status of [401, 403]) {
    const client = new ResearchClient('https://example.test', '', async () => new Response('', {status}));
    await assert.rejects(client.json('/research'), error => error instanceof ApiError && error.status === status);
  }
});

test('429 explains the deployment quota using the API detail', async () => {
  const client = new ResearchClient('https://example.test', '', async () =>
    Response.json({detail: {message: 'You have reached the Limited deployment limit of 2 research runs per UTC day. The quota resets at midnight UTC; no new investigation was started.'}}, {status: 429}));
  await assert.rejects(client.json('/research/id/continue'), error =>
    error instanceof ApiError && error.status === 429 &&
    error.message.includes('resets at midnight UTC'));
});

test('SSE handles split UTF-8, CRLF, comments and multiline data', async () => {
  const bytes = new TextEncoder().encode(': comment\r\nid: 1\r\nevent: trace\r\ndata: héllo\r\ndata: world\r\n\r\nevent: state\ndata: done\n\n');
  let index = 0;
  const body = new ReadableStream({pull(controller) {
    if (index === bytes.length) controller.close(); else controller.enqueue(bytes.slice(index, ++index));
  }});
  const client = new ResearchClient('https://example.test', 'secret', async () => new Response(body, {headers: {'Content-Type': 'text/event-stream'}}));
  const events = [];
  await client.stream('/events', event => { events.push(event); });
  assert.deepEqual(events, [{id: '1', event: 'trace', data: 'héllo\nworld'}, {id: '', event: 'state', data: 'done'}]);
  assert.equal(parseEvent(': ignored'), null);
});

test('SSE stops consuming after a terminal event', async () => {
  const body = new ReadableStream({start(controller) {
    controller.enqueue(new TextEncoder().encode('data: done\n\ndata: ignored\n\n'));
  }});
  const client = new ResearchClient('https://example.test', '', async () => new Response(body, {headers: {'Content-Type': 'text/event-stream'}}));
  const events = [];
  await client.stream('/events', event => {events.push(event); return false;});
  assert.equal(events.length, 1);
});

test('nonlocal insecure API addresses are rejected', () => {
  assert.throws(() => new ResearchClient('http://example.test', 'secret'));
  assert.throws(() => new ResearchClient('https://secret@example.test'));
  assert.throws(() => new ResearchClient('https://example.test?token=secret'));
});
