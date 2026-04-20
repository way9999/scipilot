export const RESEARCH_STAGE_ORDER = ['focus', 'literature', 'structure', 'writing', 'complete'] as const

export type ResearchStage = (typeof RESEARCH_STAGE_ORDER)[number]

export interface ResearchWorkbenchPaper {
  record_id?: string
  title?: string
  authors?: string[]
  year?: number | string
  venue?: string
  discipline?: string
  doi?: string
  source?: string
  url?: string
  pdf_url?: string
  local_path?: string
  verified?: boolean
  downloaded?: boolean
  citation_count?: number
}

export interface ResearchProjectState {
  project_root?: string
  updated_at?: string
  current_stage?: string
  outline_frozen?: boolean
  summary?: string
  last_search?: {
    query?: string
    discipline?: string
  }
  artifacts?: Record<string, unknown>
  paths?: {
    paper_index?: string
    [key: string]: unknown
  }
  stage_status?: Record<string, string>
}

export interface ResearchRoute {
  arguments?: string
  current_stage?: string
  recommended_route?: string
  route_label?: string
  rationale?: string[]
  state_summary?: string
}

export interface ResearchWorkbenchSummary {
  paper_count: number
  verified_count: number
  downloaded_count: number
  source_counts: Record<string, number>
  discipline_counts: Record<string, number>
  year_counts: Record<string, number>
  top_papers: ResearchWorkbenchPaper[]
}

export interface Recommendation {
  type: 'action' | 'warning' | 'info' | 'suggestion'
  priority: 'high' | 'medium' | 'low'
  title: string
  description: string
  action?: string
  count?: number
  queries?: string[]
}

export interface LandscapePaper {
  paper_id?: string
  title?: string
  authors?: string[]
  year?: number
  venue?: string
  source?: string
  doi?: string
  url?: string
  tools: string[]
  methods: string[]
  metrics: string[]
  datasets: string[]
  contribution: string
  citation_count?: number
}

export interface LandscapeResult {
  topic: string
  discipline: string
  timestamp: string
  paper_count: number
  papers: LandscapePaper[]
  statistics: {
    tools: Record<string, number>
    methods: Record<string, number>
    metrics: Record<string, number>
    datasets: Record<string, number>
    years: Record<string, number>
    venues: Record<string, number>
  }
  method_clusters: Record<string, string[]>
  table_markdown: string
  mermaid_diagram: string
  trend_summary: string
}

export interface SidecarResponse<T = unknown> {
  success: boolean
  data?: T
  error?: string
}

export interface AppSettings {
  default_provider: 'llm' | 'ollama'
  default_model: string
  llm_model: string
  ollama_model: string
  image_gen_model: string
  default_discipline: string
  sidecar_auto_start: boolean
  language: 'zh' | 'en'
  api_base_urls: Record<string, string>
  api_keys: Record<string, string>
  agent_enabled: boolean
  agent_type: 'claude_code' | 'codex' | 'custom'
  agent_path: string
  agent_max_turns: number
  agent_timeout_secs: number
  agent_auto_fix: boolean
  agent_auto_supplement: boolean
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
}

export interface AssistantPreset {
  id: string
  name: string
  icon: string
  description: string
  systemPrompt: string
  color: string
}

export interface Conversation {
  id: string
  assistantId: string
  title: string
  messages: ChatMessage[]
  createdAt: string
  updatedAt: string
}

export const DISCIPLINE_OPTIONS = [
  { label: 'Generic', value: 'generic' },
  { label: 'CS / AI', value: 'cs' },
  { label: 'Physics', value: 'physics' },
  { label: 'Biology', value: 'bio' },
  { label: 'Chemistry', value: 'chemistry' },
  { label: 'Materials', value: 'materials' },
  { label: 'Energy', value: 'energy' },
  { label: 'Economics', value: 'economics' },
] as const

export function isDownloaded(paper: ResearchWorkbenchPaper): boolean {
  return Boolean(paper.downloaded || paper.local_path)
}

export function getOrderedStageEntries(
  stageStatus?: Record<string, string>
): Array<[string, string]> {
  if (stageStatus && Object.keys(stageStatus).length > 0) {
    const ranking = new Map(RESEARCH_STAGE_ORDER.map((s, i) => [s, i]))
    return Object.entries(stageStatus).sort(
      ([a], [b]) =>
        (ranking.get(a as ResearchStage) ?? 999) -
        (ranking.get(b as ResearchStage) ?? 999)
    )
  }
  return RESEARCH_STAGE_ORDER.map((s) => [s, 'pending'])
}


export interface QualityReport {
  score: number
  issues: { severity: 'high' | 'medium' | 'low'; category: string; message: string }[]
  summary: Record<string, number>
  total_checks: number
}

export interface PaperPackageResult {
  kind?: string
  markdown_path?: string
  outline_path?: string
  plan_path?: string
  prompts_path?: string
  latex_path?: string
  bib_path?: string
  json_path?: string
  html_path?: string
  output_path?: string
  project_analysis_path?: string
  artifact_paths?: string[]
  primary_path?: string | null
  artifact?: {
    title?: string
    summary?: string
    language?: string
    paper_type?: string
    target_words?: number
    actual_words?: number
    quality_meta?: {
      llm_enhanced?: boolean
      provider?: string | null
      model?: string | null
      target_words?: number
      actual_words?: number
      deduplicated?: boolean
      anti_ai_cleanup?: boolean
      section_contextualized?: boolean
      structure_guardrails?: boolean
      reference_reranked?: boolean
      cross_section_deduped?: boolean
      scaffold_compressed?: boolean
      low_signal_pruned?: boolean
      result_placeholders_standardized?: boolean
      base_sections_preserved?: boolean
      enhancer_error?: string
      refinement_round?: number
      refinement_total_rounds?: number
      chunk_count?: number
      checklist_total?: number
    }
    quality_report?: QualityReport
  }
  state?: ResearchProjectState
}

export interface WritingTaskStartResult {
  task_id: string
  status: 'running' | 'completed' | 'failed' | 'canceled'
}

export interface WritingTaskProgressEvent {
  timestamp: string
  step: number
  total: number
  label: string
  detail?: string
  phase?: string
  phase_label?: string
}

export interface WritingTaskProgress {
  step: number
  total: number
  label: string
  detail?: string
  updated_at?: string
  phase?: string
  phase_label?: string
  events?: WritingTaskProgressEvent[]
}

export interface WritingTaskStatusResult {
  task_id: string
  status: 'running' | 'completed' | 'failed' | 'canceled'
  result?: PaperPackageResult
  error?: string
  progress?: WritingTaskProgress
}

export interface ExportDocxRequest {
  artifact: 'paper' | 'proposal' | 'literature_review' | 'research_answer' | 'presentation'
  source?: string
  output?: string
  topic?: string
  question?: string
  language: 'auto' | 'zh' | 'en'
  paperType: 'general' | 'conference' | 'journal'
  targetWords?: number
  docxStyle: 'default' | 'thesis' | 'journal'
  deckType: 'proposal_review' | 'lab_update' | 'conference'
}

export interface ExportPptxRequest {
  source?: string
  output?: string
  topic?: string
  language: 'auto' | 'zh' | 'en'
  deckType: 'proposal_review' | 'lab_update' | 'conference'
}
