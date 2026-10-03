export class ApiError extends Error { status: number; }
export interface ServerEvent { event: string; id: string; data: string; }
export function parseEvent(frame: string): ServerEvent | null;
export class ResearchClient {
  constructor(base: string, token?: string, transport?: typeof fetch);
  closed: boolean;
  close(): void;
  assertActive(): void;
  request(path: string, options?: RequestInit): Promise<Response>;
  json<T = unknown>(path: string, options?: RequestInit): Promise<T>;
  stream(path: string, onEvent: (event: ServerEvent) => Promise<boolean | void> | boolean | void, signal?: AbortSignal): Promise<void>;
}
