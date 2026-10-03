/** In-memory credentials only. Never send tokens in URLs, cookies, or browser storage. */
export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export function parseEvent(frame) {
  let event = 'message'; let id = ''; const data = [];
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith(':')) continue;
    const colon = line.indexOf(':');
    const field = colon < 0 ? line : line.slice(0, colon);
    const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '');
    if (field === 'event') event = value;
    if (field === 'id' && !value.includes('\0')) id = value;
    if (field === 'data') data.push(value);
  }
  return data.length ? {event, id, data: data.join('\n')} : null;
}

export class ResearchClient {
  constructor(base, token = '', transport) {
    const url = new URL(base);
    if (url.username || url.password || url.search || url.hash ||
        !(url.protocol === 'https:' || (url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)))) {
      throw new Error('The API must use HTTPS or a local development address.');
    }
    this.base = base.replace(/\/$/, ''); this.token = token;
    this.transport = transport ?? globalThis.fetch.bind(globalThis);
    this.controller = new AbortController(); this.closed = false;
  }
  close() { this.closed = true; this.token = ''; this.controller.abort(); }
  assertActive() { if (this.closed) throw new DOMException('Session ended', 'AbortError'); }
  async request(path, options = {}) {
    this.assertActive();
    if (!path.startsWith('/') || path.startsWith('//')) throw new Error('Invalid API path');
    const headers = new Headers(options.headers);
    if (this.token) headers.set('Authorization', `Bearer ${this.token}`);
    const response = await this.transport(`${this.base}${path}`, {...options, headers,
      signal: options.signal ? AbortSignal.any([this.controller.signal, options.signal]) : this.controller.signal,
      credentials: 'omit', redirect: 'error', cache: 'no-store'});
    this.assertActive();
    if (!response.ok) {
      let detail = '';
      try {
        const payload = await response.clone().json();
        if (typeof payload?.detail === 'string') detail = payload.detail;
        else if (typeof payload?.detail?.message === 'string') detail = payload.detail.message;
      } catch {}
      const message = response.status === 401 ? 'API access is restricted. Check the server configuration.' :
        response.status === 403 ? (detail || 'Your account has read-only access.') :
        response.status === 429 ? (detail || 'The research limit has been reached. Please wait for the quota to reset before starting another investigation.') :
        detail || `Request failed (${response.status}).`;
      throw new ApiError(response.status, message);
    }
    return response;
  }
  async json(path, options = {}) {
    const response = await this.request(path, options);
    const data = await response.json(); this.assertActive(); return data;
  }
  async stream(path, onEvent, signal) {
    const response = await this.request(path, {headers: {Accept: 'text/event-stream'}, signal});
    if (!response.headers.get('content-type')?.startsWith('text/event-stream') || !response.body)
      throw new Error('The API did not return an event stream.');
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
    try {
      while (true) {
        const {value, done} = await reader.read(); this.assertActive();
        if (done) return;
        buffer += decoder.decode(value, {stream: true});
        if (buffer.length > 2_000_000) throw new Error('Progress event exceeds the size limit.');
        let boundary;
        while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
          const frame = buffer.slice(0, boundary.index);
          buffer = buffer.slice(boundary.index + boundary[0].length);
          const event = parseEvent(frame);
          if (event && await onEvent(event) === false) return;
        }
      }
    } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
  }
}
