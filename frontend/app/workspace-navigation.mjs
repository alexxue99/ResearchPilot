const validId = id => typeof id === 'string' && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(id);
const storageKey = api => `researchpilot-last-investigation:${api}`;

export function workspaceLocation(search) {
  const params = new URLSearchParams(search);
  const demo = params.get('demo');
  const id = params.get('research');
  return {demo, research: !demo && validId(id) ? id : null};
}

export function rememberInvestigation(storage, api, id) {
  if (!validId(id)) return;
  try { storage.setItem(storageKey(api), id); } catch { /* Storage can be disabled. */ }
}

export async function recentInvestigationId(client, storage, api) {
  try {
    const id = storage.getItem(storageKey(api));
    if (validId(id)) return id;
  } catch { /* The server history still works without browser storage. */ }
  const history = await client.json('/research');
  return Array.isArray(history) ? history.find(item => validId(item?.id))?.id ?? null : null;
}
