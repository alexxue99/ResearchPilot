'use client';

import { FormEvent, useCallback, useEffect, useRef, useState } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import type { EvidenceItem, ExperimentDesign, ExperimentResult, ResearchState, Source, TraceEvent } from './types';
import { ResearchClient } from './api-client.mjs';
import { normalizeExperimentMath, flattenExperimentSettings } from './experiment-math.mjs';
import { experimentStatus } from './experiment-status.mjs';
import { ReportLink, spaceReportSummary } from './report-presentation.mjs';
import { DemoGallery, type DemoEntry } from './demo-gallery';
import { demoAssetUrl, demoReportHref, validateDemoState } from './demo-assets.mjs';
import { workspaceLocation, rememberInvestigation, recentInvestigationId } from './workspace-navigation.mjs';

const API = process.env.NEXT_PUBLIC_RESEARCHPILOT_API ?? 'http://localhost:8000';
const DEMO_ONLY = process.env.NEXT_PUBLIC_RESEARCHPILOT_DEMO_ONLY === 'true' || !process.env.NEXT_PUBLIC_RESEARCHPILOT_API;
const tabs = ['Investigation', 'Sources', 'Experiments', 'Report', 'Trace'] as const;
type Tab = typeof tabs[number];
type DeploymentQuota = {enabled: boolean; mode?: string; experiments_allowed?: boolean; remaining?: number; limit?: number; resets_at?: number};
const researchStages = [
  ['interpretation', 'Interpretation'],
  ['ai_general_reasoning', 'Initial reasoning'],
  ['additional_literature_search', 'Additional literature search'],
  ['evidence_extraction', 'Evidence extraction'],
  ['experiment_planning', 'Experiment planning'],
  ['experiment_execution', 'Experiment execution'],
  ['evidence_synthesis', 'Evidence synthesis'],
  ['confidence_estimation', 'Verdict'],
  ['final_report', 'Final report'],
] as const;

function stageDisplayLabel(action: string) {
  return researchStages.find(([stage]) => stage === action)?.[1] ?? action.replaceAll('_', ' ');
}

const emptyInvestigation: ResearchState = {
  id: '', status: 'draft', confidence: 0, tool_calls: 0, model_calls: 0,
  question: '', objective: '', plan: [], assumptions: [], constraints: [],
  sources: [], evidence: [], experiments_planned: [], experiments_completed: [], unresolved_questions: [],
  conclusions: [], critique: [], artifacts: [], trace: [], report: '',
};


function traceSummary(action: string, summary: string) {
  return action === 'confidence_estimation' ? 'Qualitative assessment conducted.' : summary;
}

function displayedTrace(item: TraceEvent, state: ResearchState) {
  if (item.action !== 'investigation' || item.status !== 'running' ||
      !['completed', 'failed', 'cancelled'].includes(state.status)) {
    return {status: item.status, summary: item.summary};
  }
  const latest = [...state.trace].reverse().find(event => event.action === 'investigation');
  const status = latest?.sequence === item.sequence ? state.status : 'failed';
  return {status, summary: status === 'failed' ? 'Investigation attempt ended without completion.' : `Investigation ${status}.`};
}

function traceMarkerStatus(item: TraceEvent, status: string, latestSequence: number | undefined) {
  return item.action === 'investigation' && status === 'running' &&
    latestSequence !== undefined && item.sequence < latestSequence ? 'progressed' : status;
}

function ReportMarkdown({children, sources, demoSlug}: {children: string; sources: Source[]; demoSlug?: string | null}) {
  return <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]} components={{a: ({href, children}) => <ReportLink href={demoReportHref(demoSlug, href)} sources={sources}>{children}</ReportLink>, img: ({src, alt}) => {
    const href = typeof src === 'string' ? demoReportHref(demoSlug, src) : undefined;
    return href ? <Image unoptimized width={900} height={520} src={href} alt={alt || 'Report figure'} /> : null;
  }}}>{normalizeExperimentMath(children)}</ReactMarkdown>;
}

function ExperimentMath({children}: {children: string}) {
  return <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]} components={{p: ({children}) => <>{children}</>}}>{normalizeExperimentMath(children, {inferPlainMath: true})}</ReactMarkdown>;
}

function reportOverview(state: ResearchState) {
  const conclusion = state.conclusions[0]?.trim() ?? '';
  const assessment = state.report.match(/(?:^|\n)# (?:Assessment|Executive Summary|Findings)\s+([\s\S]*?)(?=\n# |$)/)?.[1].trim();
  const assessmentParagraphs = assessment?.split(/\n\s*\n/).map(paragraph => paragraph.trim()).filter(Boolean);
  return spaceReportSummary(assessmentParagraphs?.slice(0, 2).join('\n\n') || conclusion || 'No supported conclusion is available yet. Open the report for details and limitations.');
}

function sourceHref(source: Source): string | undefined {
  const arxivUrl = source.url?.match(/^https:\/\/(?:www\.)?arxiv\.org\/(?:abs|pdf)\/([^?#]+?)(?:\.pdf)?(?:[?#]|$)/i);
  const arxivDoi = source.doi?.match(/^10\.48550\/arxiv\.(.+)$/i)?.[1];
  const arxivId = source.arxiv_id || (source.id.startsWith('arxiv:') ? source.id.slice(6) : '') || arxivDoi || arxivUrl?.[1] || '';
  if (/^(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]*\/\d{7})(?:v\d+)?$/i.test(arxivId)) return `https://arxiv.org/abs/${arxivId}`;
  const doi = source.doi?.trim().replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, '');
  if (doi && /^10\.\d{4,9}\/\S+$/i.test(doi)) return `https://doi.org/${doi.split('/').map(encodeURIComponent).join('/')}`;
  if (source.url) {
    try {
      const url = new URL(source.url);
      if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password) return url.href;
    } catch { /* The source has no usable public URL. */ }
  }
  return undefined;
}

function sourcesByRelevance(state: ResearchState): Source[] {
  const attached = new Set(state.context_source_ids ?? []);
  const reasoning = new Set([
    ...(state.reasoning_source_ids ?? []),
    ...(state.general_reasoning ?? []).flatMap(item => item.source_ids),
  ]);
  const evidence = new Set([
    ...(state.structured_evidence ?? []).map(item => item.source_id),
    ...state.evidence.flatMap(item => item.source_id ? [item.source_id] : []),
  ]);
  const priority = (source: Source) => attached.has(source.id) ? 0
    : reasoning.has(source.id) ? 1 : evidence.has(source.id) ? 2 : 3;
  // Preserve the existing relevance order within each provenance group.
  return [...state.sources].sort((a, b) => priority(a) - priority(b));
}

export default function Home() {
  const [demos, setDemos] = useState<DemoEntry[]>([]);
  const [catalogReady, setCatalogReady] = useState(false);
  const [demoError, setDemoError] = useState('');
  const [demoSlug, setDemoSlug] = useState<string | null>(null);
  const [loadedDemoSlug, setLoadedDemoSlug] = useState<string | null>(null);
  const [showGallery, setShowGallery] = useState(true);
  const selectedDemo = demos.find(demo => demo.slug === demoSlug);
  const readOnly = Boolean(demoSlug);
  const showWorkspace = !showGallery && (!demoSlug || loadedDemoSlug === demoSlug);
  const [state, setState] = useState<ResearchState>(emptyInvestigation);
  const [active, setActive] = useState<Tab>('Investigation');
  const [query, setQuery] = useState('');
  const [feedbackText, setFeedbackText] = useState('');
  const [notice, setNotice] = useState('Enter a conjecture to start an investigation.');
  const [busy, setBusy] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [contextKind, setContextKind] = useState<'arxiv' | 'pdf'>('arxiv');
  const [contextUrl, setContextUrl] = useState('');
  const [contextFile, setContextFile] = useState<File | null>(null);
  const contextFileInputRef = useRef<HTMLInputElement | null>(null);
  const [contextTitle, setContextTitle] = useState('');
  const [pendingResearchId, setPendingResearchId] = useState<string | null>(null);
  const [researchId, setResearchId] = useState<string | null>(null);
  const [navigationVersion, setNavigationVersion] = useState(0);
  const navigationRef = useRef(0);
  const [cancelling, setCancelling] = useState(false);
  const streamRef = useRef<AbortController | null>(null);
  const streamPollRef = useRef<number | null>(null);
  const [client] = useState(() => new ResearchClient(API));
  const [deploymentQuota, setDeploymentQuota] = useState<DeploymentQuota | null>(null);
  const [reportPdf, setReportPdf] = useState<{id: string; report: string; url?: string; error?: string} | null>(null);
  const [savingDemo, setSavingDemo] = useState(false);
  const [darkMode, setDarkMode] = useState(false);
  const [themeLoaded, setThemeLoaded] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void fetch('/demos/catalog.json', {signal: controller.signal}).then(async response => {
      if (!response.ok) throw new Error('The demo catalog could not be loaded.');
      const data: DemoEntry[] = await response.json();
      if (!Array.isArray(data) || data.some(item => !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(item.slug))) throw new Error('Invalid demo catalog.');
      if (!controller.signal.aborted) { setDemos(data); setCatalogReady(true); }
    }).catch(error => { if (!controller.signal.aborted) { setDemoError(error.message); setCatalogReady(true); } });
    const restoreDemo = () => {
      navigationRef.current += 1;
      streamRef.current?.abort();
      if (streamPollRef.current !== null) window.clearInterval(streamPollRef.current);
      const location = workspaceLocation(window.location.search);
      const id = DEMO_ONLY ? null : location.research;
      setBusy(false); setPendingResearchId(null); setState(emptyInvestigation);
      setDemoSlug(location.demo); setResearchId(id);
      setNavigationVersion(value => value + 1);
      setShowGallery(!location.demo && !id);
    };
    restoreDemo();
    window.addEventListener('popstate', restoreDemo);
    return () => { controller.abort(); window.removeEventListener('popstate', restoreDemo); };
  }, []);

  useEffect(() => {
    if (!demoSlug || !catalogReady) return;
    const controller = new AbortController();
    void (async () => {
      if (!selectedDemo) throw new Error('This demo is not in the gallery.');
      const response = await fetch(demoAssetUrl(demoSlug, 'state.json'), {signal: controller.signal});
      if (!response.ok) throw new Error('This demo snapshot could not be loaded.');
      const snapshot = validateDemoState(await response.json());
      snapshot.artifacts.forEach(path => demoAssetUrl(demoSlug, path));
      if (controller.signal.aborted) return;
      setState(snapshot); setQuery(snapshot.question); setLoadedDemoSlug(demoSlug); setActive('Investigation');
      setDemoError(''); setNotice('Viewing a precomputed demonstration.');
    })().catch(error => { if (!controller.signal.aborted) setDemoError(error.message); });
    return () => controller.abort();
  }, [demoSlug, catalogReady, selectedDemo]);

  const openDemo = (slug: string) => {
    navigationRef.current += 1;
    streamRef.current?.abort();
    if (streamPollRef.current !== null) window.clearInterval(streamPollRef.current);
    setBusy(false); setDemoError(''); setResearchId(null); setDemoSlug(slug); setShowGallery(false);
    const url = new URL(window.location.href); url.search = ''; url.searchParams.set('demo', slug);
    window.history.pushState(null, '', url);
  };

  const openGallery = () => {
    navigationRef.current += 1;
    streamRef.current?.abort();
    if (streamPollRef.current !== null) window.clearInterval(streamPollRef.current);
    setBusy(false); setResearchId(null); setPendingResearchId(null);
    setShowGallery(true); setDemoSlug(null); setState(emptyInvestigation); setLoadedDemoSlug(null);
    const url = new URL(window.location.href); url.search = '';
    window.history.pushState(null, '', url);
  };

  const openLocalWorkspace = async () => {
    const revision = ++navigationRef.current;
    streamRef.current?.abort();
    if (streamPollRef.current !== null) window.clearInterval(streamPollRef.current);
    setBusy(false); setDemoSlug(null); setLoadedDemoSlug(null); setState(emptyInvestigation);
    setResearchId(null); setShowGallery(false); setActive('Investigation'); setQuery(''); setPendingResearchId(null);
    setNotice('Loading your recent investigation…');
    try {
      const id = await recentInvestigationId(client, window.localStorage, API);
      if (revision !== navigationRef.current) return;
      setResearchId(id);
      setNavigationVersion(value => value + 1);
      const url = new URL(window.location.href); url.search = '';
      if (id) url.searchParams.set('research', id);
      window.history.pushState(null, '', url);
      if (!id) setNotice('Enter a conjecture to start an investigation.');
    } catch (error) {
      if (revision === navigationRef.current) setNotice(error instanceof Error ? error.message : 'Could not load investigation history.');
    }
  };

  useEffect(() => {
    if (!DEMO_ONLY && !demoSlug && !showGallery && state.id) rememberInvestigation(window.localStorage, API, state.id);
  }, [state.id, demoSlug, showGallery]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const saved = window.localStorage.getItem('researchpilot-theme');
      setDarkMode(saved === 'dark');
      setThemeLoaded(true);
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);
  useEffect(() => {
    if (!themeLoaded) return;
    document.documentElement.dataset.theme = darkMode ? 'dark' : 'light';
    window.localStorage.setItem('researchpilot-theme', darkMode ? 'dark' : 'light');
  }, [darkMode, themeLoaded]);

  const overview = reportOverview(state);
  const currentReportPdf = reportPdf?.id === state.id && reportPdf.report === state.report ? reportPdf : null;
  const reportPdfUrl = currentReportPdf?.url ?? null;
  const reportPdfError = currentReportPdf?.error ?? '';
  const reportPdfLoading = Boolean(!readOnly && state.id && state.report && !currentReportPdf);
  const completedStages = new Set(state.trace.filter(event => event.status === 'completed').map(event => event.action));
  const currentStage = state.status === 'running'
    ? researchStages.findIndex(([action]) => !completedStages.has(action)) : -1;
  const stagesDone = researchStages.filter(([action]) => completedStages.has(action)).length;
  const synthesizedItems = (state.structured_evidence?.length ?? 0) + (state.experimental_evidence?.length ?? 0);
  const progressDetails: Record<(typeof researchStages)[number][0], {value: string; detail: string}> = {
    interpretation: {value: completedStages.has('interpretation') ? '✓' : '—', detail: 'Claim interpreted'},
    ai_general_reasoning: {value: completedStages.has('ai_general_reasoning') ? String(state.reasoning_source_ids?.length ?? 0) : '—', detail: 'Source' + (state.reasoning_source_ids?.length === 1 ? ' for reasoning' : 's for reasoning')},
    additional_literature_search: {value: completedStages.has('additional_literature_search') ? String(state.additional_literature_source_ids?.length ?? 0) : '—', detail: 'Source' + (state.additional_literature_source_ids?.length === 1 ? ' found' : 's found')},
    evidence_extraction: {value: completedStages.has('evidence_extraction') ? String(state.structured_evidence?.length ?? 0) : '—', detail: 'Finding' + (state.structured_evidence?.length === 1 ? ' extracted' : 's extracted')},
    experiment_planning: {value: completedStages.has('experiment_planning') ? String(state.experiments_planned.length) : '—', detail: 'Experiment' + (state.experiments_planned.length === 1 ? ' planned' : 's planned')},
    experiment_execution: {value: completedStages.has('experiment_execution') ? String(state.experiments_completed.filter(result => result.status === 'completed').length) : '—', detail: 'Experiment' + (state.experiments_completed.filter(result => result.status === 'completed').length === 1 ? ' completed' : 's completed')},
    evidence_synthesis: {value: completedStages.has('evidence_synthesis') ? String(synthesizedItems) : '—', detail: 'Finding' + (synthesizedItems === 1 ? ' synthesized' : 's synthesized')},
    confidence_estimation: {value: completedStages.has('confidence_estimation') ? '\u2713' : '\u2014', detail: 'Verdict given'},
    final_report: {value: completedStages.has('final_report') ? 'Ready' : '—', detail: 'Report generated'},
  };
  const contextReady = !contextOpen || (contextKind === 'arxiv' ? Boolean(contextUrl.trim()) : Boolean(contextFile));
  const refreshQuota = useCallback(async () => {
    if (DEMO_ONLY || readOnly) return;
    try { setDeploymentQuota(await client.json<DeploymentQuota>('/deployment/quota')); }
    catch { setDeploymentQuota(null); }
  }, [client, readOnly]);
  const refresh = async (id: string) => {
    const next = await client.json<ResearchState>(`/research/${id}`);
    setState(next);
    return next;
  };
  const attachStream = useCallback((id: string) => {
    streamRef.current?.abort();
    if (streamPollRef.current !== null) window.clearInterval(streamPollRef.current);
    const stream = new AbortController();
    streamRef.current = stream;
    let terminal = false;
    let refreshing = false;
    const update = async () => {
      if (stream.signal.aborted || terminal || refreshing) return;
      refreshing = true;
      try {
        const current = await client.json<ResearchState>(`/research/${id}`,
          {signal: AbortSignal.any([stream.signal, AbortSignal.timeout(5000)])});
        if (stream.signal.aborted) return;
        setState(current);
        if (['completed', 'failed', 'needs_input', 'cancelled'].includes(current.status)) {
          terminal = true;
          if (streamPollRef.current !== null) {
            window.clearInterval(streamPollRef.current);
            streamPollRef.current = null;
          }
          setBusy(false);
          setNotice(current.stop_reason === 'model_limit' ? 'LLM limit reached. The investigation finished with a partial report.' : current.status === 'needs_input' ? 'Answer the questions below to continue.' : current.status === 'completed' ? 'Investigation complete.' : current.status === 'cancelled' ? 'Investigation cancelled.' : 'Investigation stopped. Check the trace for details.');
          stream.abort();
        }
      } finally { refreshing = false; }
    };
    streamPollRef.current = window.setInterval(() => {
      void update().catch(() => {
        if (!stream.signal.aborted) setNotice('Progress update unavailable; retrying.');
      });
    }, 3000);
    void client.stream(`/research/${id}/events`, async event => {
      if (event.event === 'error') throw new Error('Progress access ended or the investigation is unavailable.');
      if (event.event !== 'state') return;
      await update();
      if (terminal) return false;
    }, stream.signal).then(() => {
      if (!terminal) throw new Error('Progress connection ended before completion.');
    }).catch(error => {
      if (client.closed || stream.signal.aborted) return;
      setNotice(`${error instanceof Error ? error.message : 'Progress connection failed'} Checking status automatically.`);
    });
    void update().catch(() => {
      if (!stream.signal.aborted) setNotice('Progress update unavailable; retrying.');
    });
  }, [client]);
  useEffect(() => () => {
    streamRef.current?.abort();
    if (streamPollRef.current !== null) window.clearInterval(streamPollRef.current);
  }, []);
  useEffect(() => {
    if (DEMO_ONLY || demoSlug || !researchId) return;
    const id = researchId;
    const controller = new AbortController();
    void client.json<ResearchState>(`/research/${id}`, {signal: controller.signal}).then(current => {
      if (controller.signal.aborted) return;
      setState(current);
      setShowGallery(false);
      setQuery(current.question);
      if (current.status === 'running') {
        setBusy(true);
        attachStream(id);
      } else if (current.status === 'completed') setNotice(current.stop_reason === 'model_limit' ? 'LLM limit reached. The investigation finished with a partial report.' : 'Investigation complete.');
      else if (current.status === 'cancelled') setNotice('Investigation cancelled.');
    }).catch(() => {
      if (!controller.signal.aborted) setNotice('Could not load this investigation.');
    });
    return () => controller.abort();
  }, [client, attachStream, researchId, demoSlug, navigationVersion]);
  useEffect(() => {
    if (DEMO_ONLY || readOnly || showGallery || new URLSearchParams(window.location.search).has('demo')) return;
    const controller = new AbortController();
    void client.json<DeploymentQuota>('/deployment/quota', {signal: controller.signal})
      .then(quota => { if (!controller.signal.aborted) setDeploymentQuota(quota); },
        () => { if (!controller.signal.aborted) setDeploymentQuota(null); });
    return () => controller.abort();
  }, [client, readOnly, showGallery]);
  useEffect(() => {
    if (!deploymentQuota?.enabled || !deploymentQuota.resets_at) return;
    const delay = Math.max(1000, deploymentQuota.resets_at * 1000 - Date.now() + 1000);
    const timer = window.setTimeout(() => { void refreshQuota(); }, delay);
    return () => window.clearTimeout(timer);
  }, [refreshQuota, deploymentQuota?.enabled, deploymentQuota?.resets_at]);

  useEffect(() => {
    if (readOnly || DEMO_ONLY || !state.id || !state.report) return;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    const id = state.id;
    const report = state.report;
    void client.request(`/research/${id}/report.pdf`, {signal: controller.signal})
      .then(response => response.blob())
      .then(blob => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setReportPdf({id, report, url: objectUrl});
      })
      .catch(error => {
        if (!controller.signal.aborted) setReportPdf({id, report, error: error instanceof Error ? error.message : 'Could not prepare the PDF.'});
      });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [client, state.id, state.report, readOnly]);

  const downloadReport = () => {
    if (!state.id || !reportPdfUrl) return;
    const link = document.createElement('a');
    link.href = reportPdfUrl;
    link.download = `researchpilot-report-${state.id}.pdf`;
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

  const downloadDemo = async () => {
    if (readOnly || DEMO_ONLY || !state.id || savingDemo) return;
    setSavingDemo(true);
    try {
      const response = await client.request(`/research/${state.id}/demo.zip`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = `researchpilot-demo-${state.id}.zip`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice('Demo ZIP downloaded. Keep it in the repository demos folder to preserve it for publication.');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Could not save the demo.');
    } finally {
      setSavingDemo(false);
    }
  };

  const attachContext = async (id: string) => {
    if (contextKind === 'arxiv') {
      await client.json(`/research/${id}/arxiv`, {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({url: contextUrl.trim()})});
      return;
    }
    if (!contextFile) throw new Error('Choose a PDF before starting the investigation.');
    if (contextFile.size > 25_000_000) throw new Error('PDF exceeds the 25 MB upload limit.');
    const content = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',')[1]);
      reader.onerror = reject;
      reader.readAsDataURL(contextFile);
    });
    await client.json(`/research/${id}/papers`, {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({filename: contextFile.name, content_base64: content,
        title: contextTitle.trim() || contextFile.name, authors: []})});
  };

  const create = async (event: FormEvent) => {
    event.preventDefault(); setResearchId(null); setBusy(true); setNotice('Initializing…');
    try {
      const next = pendingResearchId
        ? await client.json<ResearchState>(`/research/${pendingResearchId}`)
        : await client.json<ResearchState>('/research', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({question: query, execute: false})});
      setState(next); setFeedbackText(''); setActive('Investigation');
      rememberInvestigation(window.localStorage, API, next.id);
      const url = new URL(window.location.href);
      url.searchParams.set('research', next.id);
      window.history.replaceState(null, '', url);
      if (contextOpen) {
        setPendingResearchId(next.id);
        try { await attachContext(next.id); }
        catch (error) {
          setPendingResearchId(next.id); setBusy(false);
          setNotice(`${error instanceof Error ? error.message : 'Could not add paper context.'} Fix the context and try again; this investigation has not started.`);
          return;
        }
        await refresh(next.id);
      }
      setPendingResearchId(null); setContextOpen(false); setContextUrl(''); setContextFile(null); setContextTitle('');
      if (next.status === 'needs_input') { setBusy(false); setNotice('Answer the questions below to continue.'); }
      else await startExecution(next.id);
    } catch (error) { if (!client.closed) { setBusy(false); setNotice(`${error instanceof Error ? error.message : 'Backend unavailable'} No investigation data is available offline.`); } }
  };
  const startExecution = async (id: string) => {
    setNotice('Starting investigation. Trace events will appear live.');
    try {
      const accepted = await client.json<{accepted: boolean}>(`/research/${id}/continue`, {method: 'POST'});
      if (!accepted.accepted) {
        void refreshQuota();
        const current = await refresh(id);
        if (current.status === 'running') { attachStream(id); return; }
        setBusy(false);
        setNotice(current.stop_reason === 'model_limit' ? 'LLM limit reached. The investigation finished with a partial report.' : current.status === 'needs_input' ? 'Answer the questions below to continue.' : current.status === 'completed' ? 'Investigation complete.' : 'Investigation could not start. Reload it to retry.');
        return;
      }
      void refreshQuota();
      attachStream(id);
    } catch (error) { if (!client.closed) { void refreshQuota(); setBusy(false); setNotice(error instanceof Error ? error.message : 'Could not reach the API.'); } }
  };
  const rerunStage = async (path: string, message: string) => {
    if (!state.id) return;
    setBusy(true); setNotice(message);
    try {
      await client.json(`/research/${state.id}${path}`, {method: 'POST'});
      await refresh(state.id);
      attachStream(state.id);
    } catch (error) {
      setBusy(false);
      setNotice(error instanceof Error ? error.message : 'Could not start the requested stage.');
    }
  };
  const cancelResearch = async () => {
    if (!state.id || state.status !== 'running' || cancelling) return;
    setCancelling(true);
    setNotice('Cancelling investigation…');
    try {
      await client.json(`/research/${state.id}/cancel`, {method: 'POST'});
      streamRef.current?.abort();
      if (streamPollRef.current !== null) {
        window.clearInterval(streamPollRef.current);
        streamPollRef.current = null;
      }
      await refresh(state.id);
      setBusy(false);
      setNotice('Investigation cancelled.');
    } catch (error) {
      try {
        const current = await refresh(state.id);
        if (current.status === 'completed' || current.status === 'cancelled') {
          setBusy(false);
          setNotice(current.stop_reason === 'model_limit' ? 'LLM limit reached. The investigation finished with a partial report.' : current.status === 'completed' ? 'Investigation complete.' : 'Investigation cancelled.');
          return;
        }
      } catch {}
      setNotice(error instanceof Error ? error.message : 'Could not cancel the investigation.');
    } finally { setCancelling(false); }
  };
  const submitFeedback = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const message = feedbackText.trim();
    if (!state.id || !message || state.status !== 'needs_input') return;
    setBusy(true);
    try {
      const next = await client.json<ResearchState>(`/research/${state.id}/feedback`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message})});
      setState(next);
      setFeedbackText(''); setActive('Investigation');
      await startExecution(next.id);
    } catch (error) { if (!client.closed) { setBusy(false); setNotice(error instanceof Error ? error.message : 'Could not save your response.'); } }
  };
  return <main className="workspace-shell">
    <aside className="rail">
      <a className="brand" href="#top"><span className="brand-mark">R</span><span>ResearchPilot</span></a>
      <nav aria-label="Workspace sections"><button type="button" className={showGallery ? 'nav-item active' : 'nav-item'} disabled={busy} onClick={openGallery}><span className="nav-dot" />Demo gallery</button>{!DEMO_ONLY && <button type="button" className="nav-item" disabled={busy} onClick={openLocalWorkspace}><span className="nav-dot" />Local workspace</button>}{tabs.map(item => <button key={item} disabled={!showWorkspace} className={!showGallery && active === item ? 'nav-item active' : 'nav-item'} onClick={() => setActive(item)}><span className="nav-dot" />{item}<small>{item === 'Sources' ? state.sources.length : item === 'Experiments' ? state.experiments_planned.length : ''}</small></button>)}</nav>
      <div className="rail-bottom">
        <Link className="nav-item" href="/help"><span className="nav-dot" />Help</Link>
        <button type="button" className="theme-toggle" role="switch" aria-checked={darkMode} onClick={() => setDarkMode(value => !value)}><span>Dark mode</span><span className="theme-switch" aria-hidden="true"><span /></span></button>
        <div className="rail-meta"><span className="eyebrow">Investigation ID</span><strong>{state.id ? state.id.slice(0, 18) : 'None selected'}</strong><span>{state.tool_calls} tool calls | {state.model_calls} live model requests | {state.llm_calls?.filter(call => call.cache_hit).length ?? 0} reused model responses</span></div>
      </div>
    </aside>

    <section className="main-pane" id="top">
      {showGallery && <DemoGallery demos={demos} loading={!catalogReady} error={demoError} onSelect={openDemo} />}
      {!showGallery && !showWorkspace && <div className="gallery-empty"><p role={demoError ? 'alert' : 'status'}>{demoError || 'Loading demo investigation…'}</p><button type="button" onClick={openGallery}>Back to gallery</button></div>}
      {showWorkspace && <>
      {readOnly && selectedDemo && <section className="demo-banner" aria-label="Precomputed demo information"><div><span className="eyebrow">Precomputed demo · Read only</span><h2>{selectedDemo.title}</h2><p>Run {selectedDemo.created_at.slice(0, 10)} · {selectedDemo.model} · {selectedDemo.status.replaceAll('_', ' ')}</p></div><div className="demo-banner-actions"><a href={demoAssetUrl(selectedDemo.slug, 'snapshot.zip')} download>Download snapshot</a><a href="/demos/local-setup.md" download>Run locally</a><button type="button" onClick={openGallery}>Back to gallery</button></div></section>}
      {!readOnly && deploymentQuota?.enabled && <div className="deployment-quota" role="status"><strong>{deploymentQuota.remaining ?? 0} of {deploymentQuota.limit ?? 0} research runs remaining</strong><span>Resets daily at 00:00 UTC.</span></div>}
      {state.stop_reason === 'model_limit' && <div className="model-limit-notice" role="alert"><strong>LLM limit reached.</strong> {state.status === 'running' ? 'The investigation is finishing with the evidence collected so far.' : 'The investigation finished with the evidence collected so far.'} Its report and assessment may be incomplete.</div>}
      <header className="topbar"><div><h1>{active === 'Investigation' ? 'Investigation' : active}</h1></div><div className={`status-pill ${state.status}`}><span /> {state.status.replace('_', ' ')}</div></header>

      {!readOnly && state.status === 'needs_input' && <form className="feedback-card" onSubmit={submitFeedback}>
        <span className="eyebrow">Input needed</span><h2>Help clarify this investigation</h2>
        {state.unresolved_questions.length > 0 && <ul>{state.unresolved_questions.map((question, index) => <li key={`${index}-${question}`}>{question}</li>)}</ul>}
        <label htmlFor="research-feedback">Your response</label>
        <textarea id="research-feedback" value={feedbackText} onChange={event => setFeedbackText(event.target.value)} maxLength={4000} required placeholder="Answer the questions above or add the missing details" />
        <button disabled={busy || !feedbackText.trim()}>{busy ? 'Saving…' : 'Submit response'}</button>
      </form>}

      {active === 'Investigation' && <>
        {state.status === 'completed' && <section className="completion-card" aria-label="Completed investigation overview">
          <div className="completion-heading"><span className="completion-check" aria-hidden="true">✓</span><div><span className="eyebrow">{state.stop_reason === 'model_limit' ? 'Partial investigation' : 'Investigation complete'}</span><h2>Results at a glance</h2></div></div>
          <div className="completion-grid">
            <div><h3>Report summary</h3><div className="completion-summary"><ReportMarkdown sources={state.sources} demoSlug={demoSlug}>{overview}</ReportMarkdown></div><button className="completion-link" onClick={() => setActive('Report')}>Read full report <span aria-hidden="true">→</span></button></div>
            <div><h3>Sources ({state.sources.length})</h3>{state.sources.length ? <><ol className="completion-sources">{state.sources.slice(0, 3).map(source => { const href = sourceHref(source); return <li key={source.id}><a href={href ?? '#source-details'} target={href ? '_blank' : undefined} rel={href ? 'noopener noreferrer' : undefined} onClick={href ? undefined : event => { event.preventDefault(); setActive('Sources'); }}>{source.title || source.id}</a></li>; })}</ol>{state.sources.length > 3 && <p className="completion-source-count">Showing the top 3 sources.</p>}</> : <p>No literature sources were recorded for this investigation.</p>}
              {state.sources.length > 0 && <button className="completion-link" onClick={() => setActive('Sources')}>View source details <span aria-hidden="true">→</span></button>}
            </div>
          </div>
        </section>}
        {!readOnly && <form className="query-card" onSubmit={create}>
          <label htmlFor="research-query"> Conjecture</label>
          <textarea id="research-query" value={query} disabled={busy} onChange={event => { setQuery(event.target.value); setPendingResearchId(null); }} placeholder="Enter a conjecture to investigate" />
          <small>Example: Randomized Kaczmarz becomes slower when singular values decay harmonically rather than remaining approximately flat.</small>
          <button type="button" className="context-toggle" disabled={busy} aria-expanded={contextOpen} onClick={() => setContextOpen(open => !open)}>+ Add an arXiv link or PDF to provide additional context</button>
          {contextOpen && <div className="context-fields">
            <p>This paper will be added to the next investigation before analysis starts.</p>
            <div className="context-kind" role="group" aria-label="Paper context type">
              <button type="button" disabled={busy} aria-pressed={contextKind === 'arxiv'} onClick={() => setContextKind('arxiv')}>arXiv link</button>
              <button type="button" disabled={busy} aria-pressed={contextKind === 'pdf'} onClick={() => setContextKind('pdf')}>Upload PDF</button>
            </div>
            {contextKind === 'arxiv' ? <label htmlFor="context-arxiv">arXiv link<input id="context-arxiv" type="url" disabled={busy} value={contextUrl} onChange={event => setContextUrl(event.target.value)} placeholder="https://arxiv.org/abs/2401.12345" /></label>
              : <><div className="context-pdf-field"><label className="context-pdf-picker">PDF file<input ref={contextFileInputRef} type="file" disabled={busy} accept=".pdf,application/pdf" onChange={event => setContextFile(event.target.files?.[0] ?? null)} /><span>{contextFile?.name ?? 'Choose file'}</span></label>{contextFile && <button type="button" className="context-pdf-remove" disabled={busy} onClick={() => { if (contextFileInputRef.current) contextFileInputRef.current.value = ''; setContextFile(null); }}>Remove file</button>}</div><label htmlFor="context-title">Paper title (optional)<input id="context-title" disabled={busy} value={contextTitle} onChange={event => setContextTitle(event.target.value)} placeholder="Uses the filename if left blank" /></label></>}
          </div>}
          <div className="query-actions"><span role="status" aria-live="polite">{notice}</span>{state.status === 'running'
            ? <button type="button" className="cancel-button" disabled={cancelling} onClick={cancelResearch}>{cancelling ? 'Stopping…' : 'Stop investigation'}</button>
            : <button disabled={busy || query.trim().length < 4 || !contextReady}>{busy ? 'Working…' : 'Investigate conjecture'} <span aria-hidden>→</span></button>}</div>
        </form>}
        {readOnly && <section className="demo-question"><span className="eyebrow">Investigated conjecture</span><h2>{state.question}</h2></section>}
        <div className="content-grid">
          <section className="panel plan-panel"><div className="panel-heading"><span className="eyebrow">Investigation map</span><span className="score">{stagesDone} / {researchStages.length} steps</span></div>
            <ol className="plan-list">{researchStages.map(([action, label], index) => {
              const done = completedStages.has(action);
              const current = index === currentStage;
              const {value, detail} = progressDetails[action];
              const savedPlanText = state.plan[index]?.trim();
              const planText = action === 'ai_general_reasoning' && savedPlanText?.toLowerCase() === 'ai general reasoning'
                ? label : savedPlanText;
              return <li key={action} className={current ? 'current-step' : undefined} aria-current={current ? 'step' : undefined}>
                <span className="plan-step" aria-label={done ? 'Completed' : current ? 'In progress' : 'Pending'}>{done ? '✓' : String(index + 1).padStart(2, '0')}</span>
                <div className="plan-step-content"><strong>{label}</strong>{action !== 'confidence_estimation' && planText && planText.toLowerCase() !== label.toLowerCase() && <p>{planText}</p>}</div>
                <div className="plan-step-metric"><b className={done ? 'complete' : undefined}>{action === 'final_report' && done ? <a href="#full-report" onClick={event => { event.preventDefault(); setActive('Report'); }}>{value}</a> : value}</b><small><ExperimentMath>{done ? detail : current ? 'In progress' : ''}</ExperimentMath></small></div>
              </li>;
            })}</ol>
          </section>
          <section className="panel live-trace-panel" aria-label="Investigation live trace">
            <div className="activity-heading"><div><span className="eyebrow">{readOnly ? 'Recorded trace' : 'Live trace'}</span><h2>Activity</h2></div>{state.status === 'running' && <span className="pulse" />}</div>
            <div className="timeline">{state.trace.slice(-7).map(item => { const display = displayedTrace(item, state); const markerStatus = traceMarkerStatus(item, display.status, state.trace.at(-1)?.sequence); return <button className={`timeline-item ${markerStatus}`} key={item.sequence} onClick={() => setActive('Trace')}><span className="timeline-number">{String(item.sequence).padStart(2, '0')}</span><div><strong>{item.action === 'confidence_estimation' ? 'Qualitative assessment conducted.' : traceSummary(item.action, display.summary)}</strong><small>{stageDisplayLabel(item.action)}</small></div></button>; })}</div>
            <div className="run-card"><span className="eyebrow">Research state</span><strong>{state.status === 'completed' ? 'Investigation complete' : state.status === 'running' ? 'Executing tools' : state.status === 'cancelled' ? 'Investigation cancelled' : state.status === 'needs_input' ? 'Waiting for your response' : state.status === 'failed' ? 'Investigation failed' : state.status === 'draft' ? 'Awaiting conjecture' : 'Starting investigation'}</strong><div className="progress"><span style={{width: state.status === 'draft' ? '0%' : state.status === 'completed' ? '100%' : state.status === 'running' ? '62%' : '18%'}} /></div><div className="run-meta"><span>{state.trace.length} events</span><span>{state.experiments_completed.length} experiments</span></div></div>
          </section>
        </div>
      </>}

      {active === 'Sources' && <section className="section-stack" id="source-details"><div className="section-intro"><p>Only retrieved sources with persistent identifiers can support literature claims.</p></div>
        {state.sources.length ? sourcesByRelevance(state).map((source, index) => <SourceCard key={source.id} source={source} index={index} attached={(state.context_source_ids ?? []).includes(source.id)} inspected={(state.inspected_papers ?? []).includes(source.id)} review={state.literature_reviews?.find(item => item.source_id === source.id)} evidence={(state.structured_evidence ?? []).filter(item => item.source_id === source.id)} />) : <Empty label="No literature sources have been retrieved yet." />}</section>}

      {active === 'Experiments' && <section className="section-stack experiments-page">
        <div className="section-intro"><p>Inspect why each test was chosen, the exact settings and assumptions, measured outputs, and the Python used for execution. A plan without a completed run is labeled as such.</p></div>
        {!readOnly && state.experiments_planned.length > 0 && <div className="experiment-workflow"><div><strong>Evidence and report</strong><p>Use the latest recorded experiment results to update the evidence synthesis, qualitative judgment, and final report.</p></div><button type="button" disabled={busy || state.status === 'running'} onClick={() => void rerunStage('/resynthesize', 'Resynthesizing evidence and regenerating the report…')}>Resynthesize evidence</button></div>}
        {state.id && state.artifacts.map((path, index) => /\.(svg|png|jpe?g)$/i.test(path) ? readOnly && demoSlug ? <figure className="plot-card" key={`${state.id}:${index}`}><Image unoptimized width={900} height={520} src={demoAssetUrl(demoSlug, path)} alt="Recorded demo experiment results" /><figcaption>Precomputed experiment artifact</figcaption></figure> : <AuthenticatedPlot key={`${state.id}:${index}`} client={client} path={`/research/${state.id}/artifact/${index}`} /> : null)}
        {state.experiments_planned.map((design, index) => <ExperimentCard key={`${design.id}:${design.code}`} design={design} experimentsAllowed={deploymentQuota?.experiments_allowed !== false} result={state.experiments_completed.find(item => item.design_id === design.id)} evidence={state.experimental_evidence?.find(item => item.experiment_id === state.experiments_completed.find(result => result.design_id === design.id)?.id)} index={index} busy={busy || state.status === 'running'} readOnly={readOnly} onSave={async code => {
          const next = await client.json<ResearchState>(`/research/${state.id}/experiments/${design.id}/code`, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code})});
          setState(next); setNotice('Experiment code saved. Rerun it to regenerate results and graphs.');
        }} onRerun={() => rerunStage(`/experiments/${design.id}/rerun`, 'Rerunning experiment and updating the assessment and report…')} />)}
        {!state.experiments_planned.length && <Empty label="No meaningful small experiment was planned for this conjecture. Check the assessment for the reason." />}</section>}

      {active === 'Report' && <>
        <div className="report-actions">{readOnly && demoSlug ? <a href={demoAssetUrl(demoSlug, 'report.md')} download>Download report Markdown</a> : <><button type="button" onClick={() => void downloadDemo()} disabled={!state.id || busy || state.status === 'running' || savingDemo}>{savingDemo ? 'Saving demo…' : 'Save demo ZIP'}</button><button type="button" onClick={downloadReport} disabled={!reportPdfUrl}>Download LaTeX PDF</button></>}</div>
        <section id="full-report" className="report-layout">
          <article className="report-paper report-pdf-paper">
            {!readOnly && reportPdfUrl ? <iframe className="report-pdf-preview" src={reportPdfUrl} title="Typeset investigation report PDF" />
              : reportPdfLoading ? <p>Typesetting the report PDF…</p>
              : <><span className="eyebrow">Generated synthesis</span>
                  {!readOnly && reportPdfError && <p role="alert">PDF preview unavailable: {reportPdfError}</p>}
                  <div className="report-content"><ReportMarkdown sources={state.sources} demoSlug={demoSlug}>{state.report || 'The report will appear after execution and critique.'}</ReportMarkdown></div></>}
          </article>
        </section>
      </>}

      {active === 'Trace' && <section className="trace-table"><header><span>Seq</span><span>Action</span><span>Auditable summary</span><span>Status</span></header>{state.trace.map(item => { const display = displayedTrace(item, state); return <div key={item.sequence}><code>{String(item.sequence).padStart(2, '0')}</code><strong>{stageDisplayLabel(item.action)}</strong><p>{traceSummary(item.action, display.summary)}</p><span className={`trace-status ${display.status}`}>{display.status}</span></div>; })}</section>}
      </>}
    </section>

  </main>;
}

function Empty({label}: {label: string}) { return <div className="empty-state"><span>∅</span><p>{label}</p></div>; }

const sourceEvidenceGroups = [
  {relation: 'supports', title: 'Supporting evidence'},
  {relation: 'contradicts', title: 'Contradictory evidence'},
  {relation: 'qualifies', title: 'Important qualifications'},
  {relation: 'neutral', title: 'Related evidence'},
] as const;

function sourceSummary(source: Source, evidence: EvidenceItem[]): string {
  const abstract = (source.abstract ?? '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
  if (abstract) {
    if (abstract.length <= 600) return abstract;
    const boundary = abstract.lastIndexOf(' ', 599);
    return `${abstract.slice(0, boundary > 400 ? boundary : 599).trimEnd()}…`;
  }
  if (evidence.length) return `No source abstract is available. The inspected material yielded ${evidence.length} classified finding${evidence.length === 1 ? '' : 's'} listed below.`;
  return 'No abstract or classified finding is available for this candidate source.';
}

function SourceCard({source, index, attached, inspected, review, evidence}: {source: Source; index: number; attached: boolean; inspected: boolean; review?: {summary: string; relevance: string}; evidence: EvidenceItem[]}) {
  const href = sourceHref(source);
  const relationCounts = sourceEvidenceGroups.map(group => ({...group, items: evidence.filter(item => item.relation_to_conjecture === group.relation)}));
  const relationText = relationCounts.filter(group => group.items.length).map(group => `${group.items.length} ${group.title.toLowerCase()}`).join('; ');
  const keyFinding = evidence.find(item => item.relation_to_conjecture === 'contradicts')
    ?? evidence.find(item => item.relation_to_conjecture === 'supports') ?? evidence[0];
  return <article className="source-card source-detail">
    <div><span className="source-index">{String(index + 1).padStart(2, '0')}</span><span className={source.verified ? 'verified' : 'unverified'} title={source.verified ? undefined : 'This source’s information has not yet been confirmed as reliable or accurate. Check who created it, the evidence behind its claims, whether it is up to date, and whether other reliable sources agree. This does not necessarily mean the source is wrong; its claims should not be treated as established yet.'}>{source.verified ? 'Verified metadata' : 'Needs verification'}</span>{attached && <span className="verified">Attached paper · {inspected ? 'text inspected' : 'text available as context'}</span>}</div>
    <h2>{href ? <a href={href} target="_blank" rel="noopener noreferrer">{source.title}</a> : source.title}</h2><p>{source.authors.join(', ') || 'Authors unavailable'}{source.year ? ` · ${source.year}` : ''}</p>
    <section><h3>Source summary</h3><p>{sourceSummary(source, evidence)}</p>{source.abstract && <small>Summarized from the retrieved abstract; inspect the source for full context.</small>}</section>
    <section><h3>Extracted context to understand the conjecture</h3><p>{relationText ? `Relevant algorithms, theorems, and other findings help explain the conjecture. Extracted and classified: ${relationText}. A key connection is: ${keyFinding.statement}` : attached ? 'The attached paper was added as context. No passage from it was classified as a finding about this conjecture.' : 'No relevant algorithm, theorem, or other context was extracted from this candidate source.'}</p>{review && <small>Review: {review.summary}</small>}</section>
    {relationCounts.filter(group => group.items.length).map(group => <section className="source-evidence-group" key={group.relation}><h3>{group.title} ({group.items.length})</h3><ul>{group.items.map((item, itemIndex) => <li key={`${item.source_location ?? 'unknown'}-${itemIndex}`}><p>{item.statement}</p><small>{item.evidence_type} · {item.source_location || 'Location unavailable'}</small>{item.assumptions?.length > 0 && <p><strong>Assumptions:</strong> {item.assumptions.join('; ')}</p>}{item.notes && <p><strong>Evidence note:</strong> {item.notes}</p>}</li>)}</ul></section>)}
    <footer><code>{source.doi ? `DOI ${source.doi}` : source.id}</code>{href ? <a href={href} target="_blank" rel="noopener noreferrer">Open source ↗</a> : <span>Source link unavailable</span>}</footer>
  </article>;
}

function ExperimentAlgorithm({design, index}: {design: ExperimentDesign; index: number}) {
  return <section className="experiment-algorithm" aria-label={`Pseudo-code for experiment ${index + 1}`}>
    <div className="algorithm-rule" />
    <div className="algorithm-caption"><strong>Algorithm {index + 1}</strong><span><ExperimentMath>{design.name}</ExperimentMath></span></div>
    {design.algorithm_steps?.length ? <ol className="algorithm-steps">{design.algorithm_steps.map((step, stepIndex) => <li key={stepIndex}><ExperimentMath>{step}</ExperimentMath></li>)}</ol> : <p>No algorithm pseudocode was recorded for this plan.</p>}
    <div className="algorithm-rule" />
  </section>;
}

function ExperimentCard({design, experimentsAllowed, result, evidence, index, busy, readOnly, onSave, onRerun}: {design: ExperimentDesign; experimentsAllowed: boolean; result?: ExperimentResult; evidence?: {finding: string; relation_to_conjecture: string; uncertainty: string; robustness?: string; confounders?: string[]; evidence_paths?: string[]}; index: number; busy: boolean; readOnly: boolean; onSave: (code: string) => Promise<void>; onRerun: () => Promise<void>}) {
  const [codeAction, setCodeAction] = useState('');
  const [codeCopied, setCodeCopied] = useState(false);
  const copyResetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (copyResetTimer.current !== null) clearTimeout(copyResetTimer.current);
  }, []);
  const [draftCode, setDraftCode] = useState(design.code);
  const [saving, setSaving] = useState(false);
  const saveCode = async () => {
    setSaving(true); setCodeAction('');
    try { await onSave(draftCode); setCodeAction('Code saved. Existing results were cleared.'); }
    catch (error) { setCodeAction(error instanceof Error ? error.message : 'Could not save code.'); }
    finally { setSaving(false); }
  };
  const copyCode = async () => {
    try {
      await navigator.clipboard.writeText(design.code);
      setCodeAction('');
      setCodeCopied(true);
      if (copyResetTimer.current !== null) clearTimeout(copyResetTimer.current);
      copyResetTimer.current = setTimeout(() => {
        setCodeCopied(false);
        copyResetTimer.current = null;
      }, 1000);
    }
    catch { setCodeAction('Clipboard unavailable'); }
  };
  const downloadCode = () => {
    const url = URL.createObjectURL(new Blob([design.code], {type: 'text/x-python'}));
    const link = document.createElement('a');
    link.href = url; link.download = `researchpilot_experiment_${design.id}.py`;
    document.body.appendChild(link); link.click(); link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const methodOnly = (design.planning_schema_version ?? 1) >= 3;
  const compactPlan = methodOnly || Boolean(design.purpose);
  return <article className="experiment-card experiment-detail">
    <header><div><span className="eyebrow">Experiment {String(index + 1).padStart(2, '0')}</span><h2><ExperimentMath>{design.name}</ExperimentMath></h2></div><span className={result?.status === 'completed' ? 'verified' : 'unverified'}>{experimentStatus(design, result)}</span></header>
    {!result && <section className="experiment-results"><h3>Execution status</h3><p>{design.execution_error || 'This experiment has not been run. No measured outcome is available.'}</p></section>}
    {!methodOnly && <section className="experiment-purpose"><h3>Why this experiment?</h3>{design.purpose ? <p><ExperimentMath>{design.purpose}</ExperimentMath></p> : <><p><ExperimentMath>{design.rationale || 'The plan did not record a selection rationale.'}</ExperimentMath></p><p><strong>Hypothesis tested:</strong> <ExperimentMath>{design.hypothesis}</ExperimentMath></p></>}</section>}
    <ExperimentAlgorithm design={design} index={index} />
    <div className="experiment-columns">
      <section><h3>Experimental choices</h3><dl>{!compactPlan && <><dt>Independent variables</dt><dd><ExperimentMath>{design.independent_variables.join(', ') || 'Not specified'}</ExperimentMath></dd><dt>Measured outcomes</dt><dd><ExperimentMath>{design.dependent_variables.join(', ') || 'Not specified'}</ExperimentMath></dd></>}<dt>Baselines</dt><dd><ExperimentMath>{design.baselines.join(', ') || 'Not specified'}</ExperimentMath></dd><dt>Metrics</dt><dd><ExperimentMath>{design.metrics.join(', ') || 'Not specified'}</ExperimentMath></dd></dl></section>
      <section><h3>{compactPlan ? 'Working assumptions' : 'Assumptions and controls'}</h3>{!compactPlan && <h4>Working assumptions</h4>}<ul>{design.assumptions.length ? design.assumptions.map((item, i) => <li key={i}><ExperimentMath>{item}</ExperimentMath></li>) : <li>None recorded.</li>}</ul>{!compactPlan && <><h4>Held constant</h4><ul>{design.controls.map((item, i) => <li key={i}><ExperimentMath>{item}</ExperimentMath></li>)}</ul></>}</section>
    </div>
    <section><h3>Exact planned settings</h3><div className="experiment-settings">{flattenExperimentSettings(design.parameter_ranges).map(({label, value}) => <div key={label}><small>{label}</small><strong><ExperimentMath>{value}</ExperimentMath></strong></div>)}<div><small>Random seeds</small><strong>{design.seeds.join(', ') || 'None specified'}</strong></div>{design.seeds.length > 0 && <div><small>Seeds</small><strong>{design.seeds.length}</strong></div>}</div></section>
    {!methodOnly && <div className="experiment-columns"><section><h3>What would the outcomes mean?</h3>{design.purpose ? <p><ExperimentMath>{design.decision_criteria || 'Not specified'}</ExperimentMath></p> : <><p><strong>If supported:</strong> <ExperimentMath>{design.expected_behavior || 'Not specified'}</ExperimentMath></p><p><strong>If contradicted:</strong> <ExperimentMath>{design.expected_if_false || 'Not specified'}</ExperimentMath></p></>}</section><section><h3>Confounders and limits</h3><ul>{[...design.confounders, ...design.limitations].map((item, i) => <li key={i}><ExperimentMath>{item}</ExperimentMath></li>)}</ul></section></div>}
    {result && <section className="experiment-results"><h3>Recorded execution</h3><p>Run ID: <code>{result.id}</code> · Runtime: {result.runtime_seconds.toFixed(2)} seconds</p><details open><summary>Recorded outputs</summary><pre>{JSON.stringify(result.metrics, null, 2)}</pre></details><details><summary>Execution settings and software</summary><pre>{JSON.stringify({configuration: result.configuration, software_versions: result.software_versions}, null, 2)}</pre></details>{(result.stdout || result.stderr) && <details><summary>Execution log</summary><pre>{[result.stdout, result.stderr].filter(Boolean).join('\n')}</pre></details>}</section>}
    {result?.status === 'completed' && evidence && <section className="experiment-results"><h3>Interpretation</h3><p><strong>{evidence.relation_to_conjecture.charAt(0).toUpperCase() + evidence.relation_to_conjecture.slice(1)}:</strong> <ExperimentMath>{evidence.finding}</ExperimentMath></p><p><strong>Uncertainty:</strong> <ExperimentMath>{evidence.uncertainty}</ExperimentMath></p>{evidence.robustness && <p><strong>Robustness:</strong> <ExperimentMath>{evidence.robustness}</ExperimentMath></p>}{evidence.confounders?.length ? <><h4>Outcome confounders</h4><ul>{evidence.confounders.map((item, i) => <li key={i}><ExperimentMath>{item}</ExperimentMath></li>)}</ul></> : null}{evidence.evidence_paths?.length ? <p><strong>Result fields used:</strong> {evidence.evidence_paths.map((path, i) => <code key={i}>{path} </code>)}</p> : null}</section>}
    <section className="experiment-code"><div className="experiment-code-heading"><div><h3>Reusable Python code</h3><p>{readOnly ? 'Recorded script from this demo. Download it to inspect or adapt locally.' : 'Edit and save this script before running it. Saving code clears results from its previous run.'}</p></div><div><button type="button" className="experiment-copy-button" onClick={copyCode} disabled={!design.code} aria-label={codeCopied ? 'Code copied' : 'Copy code'}><span aria-hidden="true" style={{visibility: codeCopied ? 'hidden' : 'visible'}}>Copy code</span>{codeCopied && <span className="experiment-copy-checkmark" aria-hidden="true">✓</span>}</button><button type="button" onClick={downloadCode} disabled={!design.code}>Download .py</button></div></div><label className="code-editor-label" htmlFor={`code-${design.id}`}>Experiment script</label><textarea id={`code-${design.id}`} className="experiment-code-editor" spellCheck={false} value={draftCode} onChange={event => setDraftCode(event.target.value)} readOnly={readOnly} disabled={busy || saving} />{!readOnly && <div className="experiment-code-actions"><button type="button" onClick={() => void saveCode()} disabled={busy || saving || !draftCode.trim() || draftCode === design.code}>{saving ? 'Saving…' : 'Save code'}</button><button type="button" onClick={() => void onRerun()} disabled={!experimentsAllowed || busy || saving || !design.code.trim() || draftCode !== design.code}>Rerun code + update report</button></div>}{!experimentsAllowed && <p>Restricted mode permits planning and code review. Experiment execution is disabled.</p>}{draftCode !== design.code && <p>Save your changes before rerunning, copying, or downloading the code.</p>}{codeAction && <p role="status">{codeAction}</p>}{design.visualization_code && <details><summary>Generated visualization script</summary><p>This script reads the completed result.json and writes visualization.svg.</p><pre>{design.visualization_code}</pre></details>}</section>
    <footer><code>{design.id}</code><span>{result ? `${result.artifacts.length} recorded artifacts` : experimentStatus(design)}</span></footer>
  </article>;
}

function AuthenticatedPlot({client, path}: {client: ResearchClient; path: string}) {
  const [image, setImage] = useState<{client: ResearchClient; path: string; url: string; error?: string} | null>(null);
  useEffect(() => {
    let active = true; let url = ''; const controller = new AbortController();
    void client.request(path, {signal: controller.signal}).then(async response => {
      const blob = await response.blob(); client.assertActive();
      if (!active) return;
      if (!['image/svg+xml', 'image/png', 'image/jpeg'].includes(blob.type)) throw new Error('Unexpected plot format.');
      url = URL.createObjectURL(blob); setImage({client, path, url});
    }).catch(error => { if (active && !client.closed) setImage({client, path, url: '', error: error instanceof Error ? error.message : 'Plot unavailable.'}); });
    return () => { active = false; controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [client, path]);
  const current = image?.client === client && image.path === path ? image : null;
  return <figure className="plot-card">{current?.url ? <Image unoptimized width={900} height={520} src={current.url} alt="Executed experiment results" /> : <p role="status">{current?.error ?? 'Loading experiment plot…'}</p>}<figcaption>Executed experiment artifact</figcaption></figure>;
}
