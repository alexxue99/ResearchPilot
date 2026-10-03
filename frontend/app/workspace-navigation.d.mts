import type { ResearchClient } from './api-client.mjs';
export function workspaceLocation(search: string): {demo: string | null; research: string | null};
export function rememberInvestigation(storage: Storage, api: string, id: string): void;
export function recentInvestigationId(client: ResearchClient, storage: Storage, api: string): Promise<string | null>;
