export type Status = 'draft' | 'planned' | 'running' | 'needs_input' | 'completed' | 'failed' | 'cancelled';

export interface Source {
  id: string; title: string; authors: string[]; abstract: string;
  year?: number; doi?: string; arxiv_id?: string; url?: string; verified: boolean;
}

export interface EvidenceItem {
  source_id: string; citation: string; statement: string; evidence_type: string;
  relation_to_conjecture: 'supports' | 'contradicts' | 'qualifies' | 'neutral';
  source_location?: string | null; assumptions: string[]; strength: number; notes: string;
}

export interface Evidence {
  id: string; kind: 'literature' | 'computation' | 'observation' | 'hypothesis'; claim: string;
  source_id?: string; experiment_id?: string; confidence: number;
}

export interface ExperimentResult {
  id: string; design_id: string; status: string; configuration: Record<string, unknown>;
  metrics: Record<string, unknown>; runtime_seconds: number; artifacts: string[];
  stdout?: string; stderr?: string; software_versions?: Record<string, string>;
}

export interface ExperimentDesign {
  id: string; name: string; hypothesis: string; rationale: string; assumptions: string[];
  independent_variables: string[]; dependent_variables: string[]; controls: string[];
  baselines: string[]; metrics: string[]; parameter_ranges: Record<string, unknown>;
  seeds: number[]; expected_behavior: string; expected_if_false: string;
  confounders: string[]; limitations: string[]; test_type: string; code: string;
  visualization_code?: string;
  algorithm_steps?: string[];
  purpose?: string;
  decision_criteria?: string;
  additional_assumptions?: string[];
  planning_schema_version?: number;
  execution_status?: string;
  execution_error?: string;
}

export interface TraceEvent {
  sequence: number; action: string; status: string; summary: string; created_at: string; latency_ms: number;
}

export interface LLMCall {
  stage: string; model: string; input_tokens: number; output_tokens: number;
  estimated_cost_usd: number; cache_hit: boolean; status?: string;
}

export interface ResearchState {
  id: string; question: string; objective: string; status: Status; plan: string[]; assumptions: string[];
  constraints: string[]; sources: Source[]; evidence: Evidence[];
  experiments_planned: ExperimentDesign[]; experiments_completed: ExperimentResult[];
  unresolved_questions: string[]; conclusions: string[]; critique: string[]; artifacts: string[];
  trace: TraceEvent[]; confidence: number; report: string; tool_calls: number; model_calls: number;
  token_usage?: number; input_tokens?: number; output_tokens?: number; cache_hits?: number;
  stop_reason?: string | null;
  estimated_cost_usd?: number; cost_estimate_complete?: boolean; llm_calls?: LLMCall[];
  conjecture?: {normalized_statement: string; assumptions: string[]; ambiguities: string[]; experimentable: boolean} | null;
  literature_reviews?: {source_id: string; relevance: string; summary: string; limitations: string[]}[];
  general_reasoning?: {relation: 'supports' | 'contradicts'; observation: string; query: string; source_ids: string[]}[];
  reasoning_source_ids?: string[];
  additional_literature_source_ids?: string[];
  selected_source_ids?: string[];
  inspected_papers?: string[];
  context_source_ids?: string[];
  structured_evidence?: EvidenceItem[];
  experimental_evidence?: {experiment_id: string; finding: string; relation_to_conjecture: string; uncertainty: string; robustness?: string; confounders?: string[]; evidence_paths?: string[]}[];
  assessment?: {revised_conjecture?: string | null; unresolved_questions: string[]} | null;
  confidence_factors?: Record<string, number> | null;
  conjecture_judgment?: string;
  judgment_rationale?: string;
  judgment_method?: string;
  confidence_rationale?: string;
  assessment_evidence_summary?: string;
}
