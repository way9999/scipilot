import { type CSSProperties, type FC, useEffect, useMemo, useRef, useState, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import mammoth from 'mammoth'
import { useAppDispatch, useAppSelector } from '../store'
import { createConversation } from '../store/chatSlice'
import { fetchDashboard, refreshWorkbench } from '../store/researchSlice'
import LLMChat from '../components/LLMChat'
import { useT } from '../i18n/context'
import * as api from '../lib/tauri'
import type {
  ExportDocxRequest,
  ExportPptxRequest,
  PaperPackageResult,
  WritingTaskProgressEvent,
} from '../types/workbench'

type WritingTab = 'studio' | 'chat' | 'outline' | 'drafts' | 'artifacts'
type LanguageOption = 'auto' | 'zh' | 'en'
type PaperTypeOption = 'general' | 'conference' | 'journal'
type DeckTypeOption = 'proposal_review' | 'lab_update' | 'conference'
type DocxStyleOption = 'default' | 'thesis' | 'journal'
type ExportArtifactOption = 'paper' | 'proposal' | 'literature_review' | 'research_answer' | 'presentation'

const BINARY_EXTENSIONS = ['.docx', '.pptx', '.pdf']
const MARKDOWN_EXTENSIONS = ['.md', '.markdown']
const JSON_EXTENSIONS = ['.json']

function uniquePaths(paths: Array<string | null | undefined>): string[] {
  const seen = new Set<string>()
  const ordered: string[] = []

  for (const path of paths) {
    if (!path || seen.has(path)) {
      continue
    }
    seen.add(path)
    ordered.push(path)
  }

  return ordered
}

function pathList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
}

function isDraftPath(path: string): boolean {
  return /(^|\/)drafts\//.test(path)
}

function isMarkdownArtifact(path: string): boolean {
  return MARKDOWN_EXTENSIONS.some((ext) => path.toLowerCase().endsWith(ext))
}

function isJsonArtifact(path: string): boolean {
  return JSON_EXTENSIONS.some((ext) => path.toLowerCase().endsWith(ext))
}

function isRefinableDraftPath(path: string): boolean {
  if (!isMarkdownArtifact(path)) {
    return false
  }
  const normalized = path.replace(/\\/g, '/').toLowerCase()
  const name = fileName(normalized).toLowerCase()
  return !(
    name.includes('outline') ||
    name.includes('plan') ||
    name.includes('prompt') ||
    name.includes('checklist') ||
    name.includes('writing-assets')
  )
}

function fileName(path: string): string {
  return path.split('/').slice(-1)[0] || path
}

function inferArtifactKindFromPath(path: string): ExportArtifactOption | null {
  const normalized = path.replace(/\\/g, '/').toLowerCase()

  if (normalized.includes('literature-review')) {
    return 'literature_review'
  }
  if (normalized.includes('research-answer')) {
    return 'research_answer'
  }
  if (normalized.includes('proposal-draft')) {
    return 'proposal'
  }
  if (normalized.includes('research-presentation')) {
    return 'presentation'
  }
  if (
    normalized.includes('paper-draft') ||
    normalized.includes('paper-outline') ||
    normalized.includes('paper-plan') ||
    normalized.includes('paper-revision-prompts') ||
    normalized.includes('project-analysis')
  ) {
    return 'paper'
  }

  return null
}

type Page = 'dashboard' | 'assistants' | 'search' | 'papers' | 'pipeline' | 'landscape' | 'writing' | 'settings'

interface WritingProps {
  onNavigate?: (page: Page) => void
}

const Writing: FC<WritingProps> = ({ onNavigate }) => {
  const dispatch = useAppDispatch()
  const { projectState } = useAppSelector((s) => s.research)
  const { settings } = useAppSelector((s) => s.settings)
  const { conversations, activeConversationId } = useAppSelector((s) => s.chat)
  const t = useT()

  const [tab, setTab] = useState<WritingTab>('studio')
  const [topic, setTopic] = useState('')
  const [question, setQuestion] = useState('')
  const [projectPath, setProjectPath] = useState('')
  const [language, setLanguage] = useState<LanguageOption>('auto')
  const [paperType, setPaperType] = useState<PaperTypeOption>('general')
  const [targetWords, setTargetWords] = useState(15000)
  const [referenceFiles, setReferenceFiles] = useState<string[]>([])
  const [docxStyle, setDocxStyle] = useState<DocxStyleOption>('thesis')
  const [deckType, setDeckType] = useState<DeckTypeOption>('proposal_review')
  const [exportArtifact, setExportArtifact] = useState<ExportArtifactOption>('paper')
  const [outlineContent, setOutlineContent] = useState('')
  const [artifactContent, setArtifactContent] = useState<Record<string, string>>({})
  const [expandedArtifact, setExpandedArtifact] = useState<string | null>(null)
  const [runningLabel, setRunningLabel] = useState<string | null>(null)
  const [currentTaskId, setCurrentTaskId] = useState<string | null>(null)
  const [canceling, setCanceling] = useState(false)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [progressStep, setProgressStep] = useState(0)
  const [progressTotal, setProgressTotal] = useState(1)
  const [progressLabel, setProgressLabel] = useState('')
  const [progressDetail, setProgressDetail] = useState('')
  const [progressEvents, setProgressEvents] = useState<WritingTaskProgressEvent[]>([])
  const [progressUpdatedAt, setProgressUpdatedAt] = useState<string | null>(null)
  const taskStartRef = useRef<number>(0)
  const elapsedTimerRef = useRef<number | null>(null)
  const [result, setResult] = useState<PaperPackageResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pollTimerRef = useRef<number | null>(null)
  const [updateAvailable, setUpdateAvailable] = useState<string | null>(null)

  const writingConversation = conversations.find(
    (conversation) => conversation.id === activeConversationId && conversation.assistantId === 'writing-inline'
  )

  useEffect(() => {
    if (!projectState) {
      void dispatch(fetchDashboard())
    }
  }, [dispatch, projectState])

  useEffect(() => {
    if (tab === 'chat' && !writingConversation) {
      dispatch(createConversation({ assistantId: 'writing-inline', title: t.writing_title }))
    }
  }, [dispatch, tab, t, writingConversation])

  useEffect(() => {
    void api
      .getProjectRoot()
      .then((root) => {
        if (!projectPath.trim()) {
          setProjectPath(root)
        }
      })
      .catch(() => {})
  }, [projectPath])

  useEffect(() => {
    return () => {
      if (pollTimerRef.current) {
        window.clearInterval(pollTimerRef.current)
      }
      if (elapsedTimerRef.current) {
        window.clearInterval(elapsedTimerRef.current)
      }
    }
  }, [])

  useEffect(() => {
    api.checkForUpdates().then((info) => {
      if (info.available) setUpdateAvailable(info.version ?? 'new')
    }).catch(() => {})
  }, [])

  const systemContext = `You are a research writing assistant for SciPilot.
Current research stage: ${projectState?.current_stage || 'focus'}.
${projectState?.summary ? `Project summary: ${projectState.summary}` : ''}
Help the user with research writing tasks such as drafting, outlining, reviewing, and refining academic text.
Always use proper academic tone and cite sources when possible. Output in Markdown format.`

  const artifactList = useMemo(() => {
    if (!result) return []
    return uniquePaths(
      result.artifact_paths ?? [
        result.markdown_path,
        result.outline_path,
        result.plan_path,
        result.prompts_path,
        result.latex_path,
        result.bib_path,
        result.json_path,
        result.html_path,
        result.output_path,
        result.project_analysis_path,
      ]
    )
  }, [result])

  const projectPaths = projectState?.paths as Record<string, unknown> | undefined

  const workspaceDrafts = useMemo(
    () =>
      uniquePaths([
        typeof projectPaths?.outline === 'string' ? projectPaths.outline : null,
        ...pathList(projectPaths?.drafts),
        ...artifactList.filter(isDraftPath),
      ]),
    [artifactList, projectPaths]
  )

  const workspaceArtifacts = useMemo(
    () =>
      uniquePaths([
        ...artifactList.filter((path) => !isDraftPath(path)),
        ...pathList(projectPaths?.output),
      ]),
    [artifactList, projectPaths]
  )

  const workspaceFileCount = workspaceDrafts.length + workspaceArtifacts.length
  const selectedArtifactKind = expandedArtifact ? inferArtifactKindFromPath(expandedArtifact) : null
  const selectedMarkdownSource = expandedArtifact && isMarkdownArtifact(expandedArtifact) ? expandedArtifact : undefined
  const selectedRefinementSource = expandedArtifact && isRefinableDraftPath(expandedArtifact) ? expandedArtifact : undefined
  const workspacePresentationPayload = useMemo(
    () =>
      [...workspaceArtifacts, ...workspaceDrafts].find(
        (path) => inferArtifactKindFromPath(path) === 'presentation' && isJsonArtifact(path)
      ),
    [workspaceArtifacts, workspaceDrafts]
  )
  const selectedPresentationSource =
    expandedArtifact && selectedArtifactKind === 'presentation' && isJsonArtifact(expandedArtifact)
      ? expandedArtifact
      : undefined
  const effectivePresentationSource =
    selectedPresentationSource || (selectedArtifactKind === 'presentation' ? workspacePresentationPayload : undefined)
  const refinementSource = useMemo(
    () =>
      selectedRefinementSource ||
      workspaceDrafts.find(isRefinableDraftPath) ||
      workspaceArtifacts.find(isRefinableDraftPath),
    [selectedRefinementSource, workspaceArtifacts, workspaceDrafts]
  )

  const exportArtifactOptions: Array<{ value: ExportArtifactOption; label: string }> = [
    { value: 'paper', label: t.writing_export_artifact_paper },
    { value: 'proposal', label: t.writing_export_artifact_proposal },
    { value: 'literature_review', label: t.writing_export_artifact_literature_review },
    { value: 'research_answer', label: t.writing_export_artifact_research_answer },
    { value: 'presentation', label: t.writing_export_artifact_presentation },
  ]

  const deckTypeOptions: Array<{ value: DeckTypeOption; label: string }> = [
    { value: 'proposal_review', label: t.writing_deck_proposal_review },
    { value: 'lab_update', label: t.writing_deck_lab_update },
    { value: 'conference', label: t.writing_deck_conference },
  ]

  const docxStyleOptions: Array<{ value: DocxStyleOption; label: string }> = [
    { value: 'default', label: t.writing_docx_style_default },
    { value: 'thesis', label: t.writing_docx_style_thesis },
    { value: 'journal', label: t.writing_docx_style_journal },
  ]

  const deckTypeLabel =
    exportArtifact === 'presentation' ? t.writing_deck_type_label : t.writing_deck_type_optional_label
  const selectedArtifactLabel = exportArtifactOptions.find((option) => option.value === selectedArtifactKind)?.label

  const qualityBadges = useMemo(() => {
    const artifact = result?.artifact
    const meta = artifact?.quality_meta
    if (!artifact && !meta) {
      return []
    }

    const badges: string[] = []
    const actualWords = artifact?.actual_words ?? meta?.actual_words
    const targetLength = artifact?.target_words ?? meta?.target_words

    if (typeof actualWords === 'number' && actualWords > 0) {
      badges.push(`${t.writing_actual_words_label} ${actualWords}`)
    }
    if (typeof targetLength === 'number' && targetLength > 0) {
      badges.push(`${t.writing_target_words_label} ${targetLength}`)
    }
    if (meta) {
      badges.push(meta.llm_enhanced ? t.writing_quality_llm : t.writing_quality_local)
      if (meta.deduplicated || meta.anti_ai_cleanup) {
        badges.push(t.writing_quality_cleanup)
      }
      if (meta.section_contextualized || meta.reference_reranked) {
        badges.push(t.writing_quality_evidence)
      }
      if (meta.structure_guardrails || meta.result_placeholders_standardized) {
        badges.push(t.writing_quality_slots)
      }
      if (meta.cross_section_deduped || meta.scaffold_compressed || meta.low_signal_pruned) {
        badges.push(t.writing_quality_concise)
      }
      const providerLabel = [meta.provider, meta.model].filter(Boolean).join(' / ')
      if (providerLabel) {
        badges.push(providerLabel)
      }
    }

    return badges
  }, [result, t])

  const tabStyle = (active: boolean): CSSProperties => ({
    padding: '8px 16px',
    borderRadius: 10,
    border: 'none',
    background: active ? 'var(--accent)' : 'transparent',
    color: active ? '#fff' : 'var(--text-secondary)',
    cursor: 'pointer',
    fontWeight: active ? 700 : 500,
    fontSize: 13,
  })

  const buttonStyle = (primary = false): CSSProperties => ({
    padding: '10px 14px',
    borderRadius: 10,
    border: primary ? '1px solid var(--accent)' : '1px solid var(--border)',
    background: primary ? 'var(--accent)' : 'var(--bg-secondary)',
    color: primary ? '#fff' : 'var(--text-primary)',
    cursor: 'pointer',
    fontWeight: 700,
    fontSize: 13,
  })

  const fieldStyle: CSSProperties = {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  }

  const inputStyle: CSSProperties = {
    padding: '10px 12px',
    borderRadius: 10,
    border: '1px solid var(--border)',
    background: 'var(--bg-primary)',
    color: 'var(--text-primary)',
  }

  const clearPolling = () => {
    if (pollTimerRef.current) {
      window.clearInterval(pollTimerRef.current)
      pollTimerRef.current = null
    }
  }

  const clearElapsed = () => {
    if (elapsedTimerRef.current) {
      window.clearInterval(elapsedTimerRef.current)
      elapsedTimerRef.current = null
    }
  }

  const startElapsed = () => {
    clearElapsed()
    taskStartRef.current = Date.now()
    setElapsedSeconds(0)
    elapsedTimerRef.current = window.setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - taskStartRef.current) / 1000))
    }, 1000)
  }

  const getEstimatedTime = (label: string | null): string | null => {
    if (!label) return null
    const estimates: Record<string, [number, number]> = {
      [t.writing_card_paper_title]: [3, 8],
      [t.writing_card_project_title]: [5, 12],
      [t.writing_card_proposal_title]: [2, 5],
      [t.writing_card_review_title]: [3, 8],
      [t.writing_card_refine_title]: [2, 4],
      [t.writing_card_qa_title]: [1, 3],
      [t.writing_card_presentation_title]: [2, 5],
      [t.writing_action_export_docx]: [0.5, 2],
      [t.writing_action_export_pptx]: [0.5, 2],
    }
    const range = estimates[label]
    if (!range) return null
    const [lo, hi] = range
    if (lo < 1) return `~${Math.round(hi * 60)}s`
    return `${lo}-${hi}min`
  }

  const formatElapsed = (seconds: number): string => {
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return m > 0 ? `${m}:${String(s).padStart(2, '0')}` : `${s}s`
  }

  const formatProgressTimestamp = (value: string | null | undefined): string | null => {
    if (!value) return null
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return null
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  }

  const isBinaryArtifact = (path: string) => BINARY_EXTENSIONS.some((ext) => path.toLowerCase().endsWith(ext))
  const isDocxArtifact = (path: string) => path.toLowerCase().endsWith('.docx')

  const [docxHtml, setDocxHtml] = useState<Record<string, string>>({})

  const loadArtifact = async (path: string) => {
    if (isDocxArtifact(path)) {
      try {
        const b64 = await api.readProjectFileBinary(path)
        const binary = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
        const result = await mammoth.convertToHtml({ arrayBuffer: binary.buffer })
        const html = result.value
        setDocxHtml((prev) => ({ ...prev, [path]: html }))
        setArtifactContent((prev) => ({ ...prev, [path]: '__docx_html__' }))
        return html
      } catch (err) {
        const fallback = `${t.writing_binary_preview}\n\n${path}\n\n${err}`
        setArtifactContent((prev) => ({ ...prev, [path]: fallback }))
        return fallback
      }
    }
    if (isBinaryArtifact(path)) {
      const fallback = `${t.writing_binary_preview}\n\n${path}`
      setArtifactContent((prev) => ({ ...prev, [path]: fallback }))
      return fallback
    }
    try {
      const content = await api.readProjectFile(path)
      setArtifactContent((prev) => ({ ...prev, [path]: content }))
      return content
    } catch {
      const fallback = t.writing_read_failed
      setArtifactContent((prev) => ({ ...prev, [path]: fallback }))
      return fallback
    }
  }

  const hydrateResult = async (payload: PaperPackageResult) => {
    setResult(payload)
    if (payload.outline_path) {
      const content = await loadArtifact(payload.outline_path)
      setOutlineContent(content)
    }
    const nextPath = payload.primary_path || payload.markdown_path || payload.output_path || payload.json_path || null
    if (nextPath) {
      setExpandedArtifact(nextPath)
      await loadArtifact(nextPath)
    }
  }

  useEffect(() => {
    const outlinePath =
      (typeof projectPaths?.outline === 'string' && projectPaths.outline) ||
      workspaceDrafts.find((path) => /outline/i.test(fileName(path))) ||
      ''

    if (!outlinePath) {
      setOutlineContent('')
      return
    }

    void api
      .readProjectFile(outlinePath)
      .then(setOutlineContent)
      .catch(() => {
        setOutlineContent(`${t.writing_outline_title}: ${outlinePath}\n${t.writing_read_failed}`)
      })
  }, [projectPaths, t, workspaceDrafts])

  useEffect(() => {
    const availablePaths = [...workspaceDrafts, ...workspaceArtifacts]
    if (availablePaths.length === 0) {
      if (expandedArtifact) {
        setExpandedArtifact(null)
      }
      return
    }

    if (expandedArtifact && availablePaths.includes(expandedArtifact)) {
      return
    }

    const nextPath = result?.primary_path || result?.markdown_path || workspaceDrafts[0] || workspaceArtifacts[0] || null
    if (nextPath) {
      setExpandedArtifact(nextPath)
      if (!artifactContent[nextPath]) {
        void loadArtifact(nextPath)
      }
    }
  }, [
    artifactContent,
    expandedArtifact,
    result?.markdown_path,
    result?.primary_path,
    workspaceArtifacts,
    workspaceDrafts,
  ])

  const openPreview = async (path: string, nextTab: Extract<WritingTab, 'drafts' | 'artifacts'> = 'artifacts') => {
    setExpandedArtifact(path)
    setTab(nextTab)
    await loadArtifact(path)
  }

  const finalizeTask = () => {
    clearPolling()
    clearElapsed()
    setCurrentTaskId(null)
    setRunningLabel(null)
    setCanceling(false)
    setProgressStep(0)
    setProgressTotal(1)
    setProgressLabel('')
    setProgressDetail('')
    setProgressEvents([])
    setProgressUpdatedAt(null)
  }

  const startPolling = (taskId: string) => {
    clearPolling()
    pollTimerRef.current = window.setInterval(() => {
      void (async () => {
        try {
          const response = await api.getWritingTaskStatus(taskId)
          if (!response.success || !response.data) {
            throw new Error(response.error || t.writing_error_generation_failed)
          }

          const status = response.data.status
          if (status === 'running') {
            // Update progress from backend
            const p = response.data.progress
            if (p) {
              setProgressStep(p.step ?? 0)
              setProgressTotal(p.total ?? 1)
              setProgressLabel(p.label ?? '')
              setProgressDetail(p.detail ?? '')
              setProgressEvents(Array.isArray(p.events) ? p.events.slice(-6).reverse() : [])
              setProgressUpdatedAt(p.updated_at ?? null)
            }
            return
          }

          if (status === 'completed' && response.data.result) {
            await hydrateResult(response.data.result)
            await dispatch(refreshWorkbench())
            await dispatch(fetchDashboard())
            const nextPrimaryPath = response.data.result.primary_path || response.data.result.markdown_path || ''
            if (response.data.result.outline_path) {
              setTab('outline')
            } else if (isDraftPath(nextPrimaryPath)) {
              setTab('drafts')
            } else {
              setTab('artifacts')
            }
            setError(null)
          } else if (status === 'canceled') {
            setError(response.data.error || t.writing_task_cancelled)
          } else {
            // failed — include step info in error
            const stepInfo = response.data.progress ? ` (Step ${response.data.progress.step}/${response.data.progress.total}: ${response.data.progress.label})` : ''
            setError((response.data.error || t.writing_error_generation_failed) + stepInfo)
          }

          finalizeTask()
        } catch (pollError) {
          setError(pollError instanceof Error ? pollError.message : t.writing_error_generation_failed)
          finalizeTask()
        }
      })()
    }, 1000)
  }

  const launchTask = async (
    label: string,
    starter: () => Promise<{ success: boolean; data?: { task_id?: string }; error?: string }>
  ) => {
    if (currentTaskId || runningLabel) {
      setError(t.writing_task_running)
      return
    }
    setError(null)
    setRunningLabel(label)
    setCanceling(false)
    setProgressStep(0)
    setProgressTotal(1)
    setProgressLabel('')
    setProgressDetail('')
    setProgressEvents([])
    setProgressUpdatedAt(null)
    startElapsed()

    try {
      const response = await starter()
      if (!response.success || !response.data?.task_id) {
        throw new Error(response.error || t.writing_error_generation_failed)
      }
      setCurrentTaskId(response.data.task_id)
      startPolling(response.data.task_id)
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : t.writing_error_generation_failed)
      finalizeTask()
    }
  }

  const requireTopic = () => {
    if (!topic.trim()) {
      setError(t.writing_error_topic_required)
      return false
    }
    return true
  }

  const requireQuestion = () => {
    if (!question.trim()) {
      setError(t.writing_error_question_required)
      return false
    }
    return true
  }

  const runPaperDraft = async () => {
    await launchTask(t.writing_card_paper_title, () =>
      api.startGeneratePaperDraft(topic.trim(), language, paperType, targetWords,
        referenceFiles.length > 0 ? referenceFiles : undefined)
    )
  }

  const runProjectPaper = async () => {
    if (!projectPath.trim()) {
      setError(t.writing_error_project_required)
      return
    }
    await launchTask(t.writing_card_project_title, () =>
      api.startGeneratePaperFromProject(projectPath.trim(), topic.trim(), language, paperType, targetWords,
        referenceFiles.length > 0 ? referenceFiles : undefined)
    )
  }

  const runProposal = async () => {
    if (!requireTopic()) return
    await launchTask(t.writing_card_proposal_title, () => api.startGenerateProposal(topic.trim(), language))
  }

  const runLiteratureReview = async () => {
    if (!requireTopic()) return
    await launchTask(t.writing_card_review_title, () => api.startGenerateLiteratureReview(topic.trim(), language))
  }

  const runRefinement = async () => {
    if (!refinementSource) {
      setError(t.writing_error_refine_source_required)
      return
    }
    await launchTask(t.writing_card_refine_title, () => api.startRefineDraft(refinementSource, language))
  }

  const runResearchQa = async () => {
    if (!requireQuestion()) return
    await launchTask(t.writing_card_qa_title, () => api.startAnswerResearchQuestion(question.trim(), language))
  }

  const runPresentation = async () => {
    if (!requireTopic()) return
    await launchTask(t.writing_card_presentation_title, () => api.startGeneratePresentation(topic.trim(), language, deckType))
  }

  const runExportDocx = async () => {
    const payload: ExportDocxRequest = {
      artifact: selectedMarkdownSource ? selectedArtifactKind ?? exportArtifact : exportArtifact,
      source: selectedMarkdownSource,
      output: undefined,
      topic: topic.trim() || undefined,
      question: question.trim() || undefined,
      language,
      paperType: paperType,
      targetWords,
      docxStyle,
      deckType: exportArtifact === 'presentation' ? deckType : 'proposal_review',
    }
    await launchTask(t.writing_action_export_docx, () => api.startExportDocx(payload))
  }

  const runExportPptx = async () => {
    const payload: ExportPptxRequest = {
      source: effectivePresentationSource,
      output: undefined,
      topic: topic.trim() || undefined,
      language,
      deckType,
    }
    await launchTask(t.writing_action_export_pptx, () => api.startExportPptx(payload))
  }

  const handleCancel = async () => {
    if (!currentTaskId || canceling) return
    setCanceling(true)
    try {
      const response = await api.cancelWritingTask(currentTaskId)
      if (!response.success) {
        throw new Error(response.error || t.writing_error_generation_failed)
      }
      setError(response.data?.error || t.writing_task_cancelled)
    } catch (cancelError) {
      setError(cancelError instanceof Error ? cancelError.message : t.writing_error_generation_failed)
    } finally {
      finalizeTask()
    }
  }

  const handleBrowseProject = async () => {
    try {
      const selected = await api.pickDirectory(projectPath.trim() || undefined)
      if (selected) {
        setProjectPath(selected)
      }
    } catch (pickError) {
      setError(pickError instanceof Error ? pickError.message : String(pickError))
    }
  }

  const handleRemoveReference = (path: string) => {
    setReferenceFiles(prev => prev.filter(f => f !== path))
  }

  const handleDeleteFile = async (relPath: string) => {
    try {
      await api.deleteProjectFile(relPath)
      if (expandedArtifact === relPath) {
        setExpandedArtifact(null)
        setArtifactContent(prev => { const next = { ...prev }; delete next[relPath]; return next })
      }
      await dispatch(refreshWorkbench())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const [refBtnPulse, setRefBtnPulse] = useState(false)
  const handleAddReferencesAnimated = useCallback(async () => {
    setRefBtnPulse(true)
    try {
      const selected = await api.pickReferenceFiles()
      if (selected && selected.length > 0) {
        setReferenceFiles(prev => {
          const existing = new Set(prev)
          return [...prev, ...selected.filter(f => !existing.has(f))]
        })
      }
    } catch (pickError) {
      setError(pickError instanceof Error ? pickError.message : String(pickError))
    } finally {
      setTimeout(() => setRefBtnPulse(false), 400)
    }
  }, [])

  const cards: Array<{ title: string; description: string; action: string; onClick: () => Promise<void>; accent: string }> = [
    { title: t.writing_card_paper_title, description: t.writing_card_paper_desc, action: t.writing_generate_from_topic, onClick: runPaperDraft, accent: '#6366f1' },
    { title: t.writing_card_project_title, description: t.writing_card_project_desc, action: t.writing_generate_from_project, onClick: runProjectPaper, accent: '#0f766e' },
    { title: t.writing_card_proposal_title, description: t.writing_card_proposal_desc, action: t.writing_action_generate_proposal, onClick: runProposal, accent: '#7c3aed' },
    { title: t.writing_card_review_title, description: t.writing_card_review_desc, action: t.writing_action_generate_review, onClick: runLiteratureReview, accent: '#ea580c' },
    { title: t.writing_card_refine_title, description: t.writing_card_refine_desc, action: t.writing_action_refine_draft, onClick: runRefinement, accent: '#9333ea' },
    { title: t.writing_card_qa_title, description: t.writing_card_qa_desc, action: t.writing_action_answer_question, onClick: runResearchQa, accent: '#2563eb' },
    { title: t.writing_card_presentation_title, description: t.writing_card_presentation_desc, action: t.writing_action_generate_presentation, onClick: runPresentation, accent: '#059669' },
    { title: t.writing_card_export_title, description: t.writing_card_export_desc, action: t.writing_action_export_docx, onClick: runExportDocx, accent: '#dc2626' },
  ]

  const latestSummary = error || result?.artifact?.summary || projectState?.summary || t.writing_workspace_desc
  const previewContent = expandedArtifact ? artifactContent[expandedArtifact] ?? t.writing_loading : t.writing_preview_placeholder

  const renderPreviewPane = () => (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 12 }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>
            {expandedArtifact ? fileName(expandedArtifact) : t.writing_preview_title}
          </h2>
          {expandedArtifact && (
            <>
              <div style={{ marginTop: 6, fontSize: 12, color: 'var(--text-tertiary)', lineHeight: 1.6 }}>
                {t.writing_export_source_label}:{' '}
                {selectedMarkdownSource
                  ? fileName(selectedMarkdownSource)
                  : effectivePresentationSource && selectedArtifactKind === 'presentation'
                    ? fileName(effectivePresentationSource)
                    : t.writing_export_source_default}
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
                {selectedArtifactLabel && (
                  <span
                    style={{
                      padding: '4px 10px',
                      borderRadius: 999,
                      fontSize: 11,
                      fontWeight: 700,
                      background: 'rgba(99,102,241,0.12)',
                      color: 'var(--accent)',
                    }}>
                    {selectedArtifactLabel}
                  </span>
                )}
                {selectedMarkdownSource && (
                  <span
                    style={{
                      padding: '4px 10px',
                      borderRadius: 999,
                      fontSize: 11,
                      fontWeight: 700,
                      background: 'rgba(5,150,105,0.12)',
                      color: '#047857',
                    }}>
                    {t.writing_export_ready_docx}
                  </span>
                )}
                {effectivePresentationSource && selectedArtifactKind === 'presentation' && (
                  <span
                    style={{
                      padding: '4px 10px',
                      borderRadius: 999,
                      fontSize: 11,
                      fontWeight: 700,
                      background: 'rgba(37,99,235,0.12)',
                      color: '#1d4ed8',
                    }}>
                    {t.writing_export_ready_pptx}
                  </span>
                )}
              </div>
            </>
          )}
        </div>

        <div style={{ display: 'flex', gap: 8 }}>
          {expandedArtifact && (
            <button style={buttonStyle(false)} onClick={() => void api.showInFileManager(expandedArtifact)}>
              {t.writing_open_in_folder}
            </button>
          )}
          {expandedArtifact && isBinaryArtifact(expandedArtifact) && (
            <button style={buttonStyle(false)} onClick={() => void api.openFileInSystem(expandedArtifact)}>
              {t.writing_open_in_system}
            </button>
          )}
          {selectedRefinementSource && (
            <button style={buttonStyle(false)} disabled={Boolean(runningLabel)} onClick={() => void runRefinement()}>
              {t.writing_action_refine_draft}
            </button>
          )}
          {selectedMarkdownSource && (
            <button style={buttonStyle(true)} disabled={Boolean(runningLabel)} onClick={() => void runExportDocx()}>
              {t.writing_action_export_docx}
            </button>
          )}
          {effectivePresentationSource && selectedArtifactKind === 'presentation' && (
            <button style={buttonStyle(false)} disabled={Boolean(runningLabel)} onClick={() => void runExportPptx()}>
              {t.writing_action_export_pptx}
            </button>
          )}
        </div>
      </div>
      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflow: 'auto',
          borderRadius: 12,
          background: 'var(--bg-secondary)',
        }}>
        {expandedArtifact ? (
          isDocxArtifact(expandedArtifact) && docxHtml[expandedArtifact] ? (
            <div
              style={{ padding: 18, color: 'var(--text-primary)', lineHeight: 1.8, fontSize: 14 }}
              dangerouslySetInnerHTML={{ __html: docxHtml[expandedArtifact] }}
            />
          ) : isMarkdownArtifact(expandedArtifact) ? (
            <div style={{ padding: 18, color: 'var(--text-primary)', lineHeight: 1.8, fontSize: 14 }}>
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  a: (props) => <a {...props} style={{ color: 'var(--accent)' }} />,
                  code: ({ children, ...props }) => (
                    <code
                      {...props}
                      style={{
                        padding: '2px 6px',
                        borderRadius: 6,
                        background: 'rgba(15, 23, 42, 0.08)',
                        fontSize: '0.92em',
                      }}>
                      {children}
                    </code>
                  ),
                  pre: ({ children, ...props }) => (
                    <pre
                      {...props}
                      style={{
                        overflow: 'auto',
                        padding: 14,
                        borderRadius: 10,
                        background: 'rgba(15, 23, 42, 0.08)',
                      }}>
                      {children}
                    </pre>
                  ),
                  table: ({ children, ...props }) => (
                    <table {...props} style={{ width: '100%', borderCollapse: 'collapse' }}>
                      {children}
                    </table>
                  ),
                  th: ({ children, ...props }) => (
                    <th {...props} style={{ textAlign: 'left', padding: '8px 10px', borderBottom: '1px solid var(--border)' }}>
                      {children}
                    </th>
                  ),
                  td: ({ children, ...props }) => (
                    <td {...props} style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)' }}>
                      {children}
                    </td>
                  ),
                }}>
                {previewContent}
              </ReactMarkdown>
            </div>
          ) : (
            <pre
              style={{
                height: '100%',
                minHeight: 0,
                margin: 0,
                padding: 16,
                overflow: 'auto',
                color: 'var(--text-primary)',
                whiteSpace: 'pre-wrap',
                lineHeight: 1.7,
                fontSize: 13,
              }}>
              {previewContent}
            </pre>
          )
        ) : (
          <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)' }}>
            {t.writing_preview_placeholder}
          </div>
        )}
      </div>
    </div>
  )

  const renderFileBrowser = (
    title: string,
    paths: string[],
    emptyText: string,
    nextTab: Extract<WritingTab, 'drafts' | 'artifacts'>
  ) => (
    <div style={{ height: '100%', padding: 18, display: 'grid', gridTemplateColumns: '340px 1fr', gap: 16, overflow: 'hidden' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, overflow: 'auto' }}>
        <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>{title}</h2>
        {paths.length > 0 ? (
          paths.map((artifactPath) => (
            <div key={artifactPath} style={{ position: 'relative' }}>
              <button
                onClick={() => void openPreview(artifactPath, nextTab)}
                style={{
                  textAlign: 'left',
                  width: '100%',
                  padding: '12px 14px',
                  paddingRight: 52,
                  borderRadius: 12,
                  border: `1px solid ${expandedArtifact === artifactPath ? 'var(--accent)' : 'var(--border)'}`,
                  background: expandedArtifact === artifactPath ? 'var(--accent-bg)' : 'var(--bg-secondary)',
                  color: 'var(--text-primary)',
                  cursor: 'pointer',
                }}>
                <div style={{ fontSize: 13, fontWeight: 700 }}>{fileName(artifactPath)}</div>
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 4 }}>{artifactPath}</div>
              </button>
              <button
                title={t.writing_open_in_folder}
                onClick={(e) => { e.stopPropagation(); void api.showInFileManager(artifactPath) }}
                style={{
                  position: 'absolute', right: 28, top: 8,
                  background: 'none', border: 'none', cursor: 'pointer',
                  color: 'var(--text-tertiary)', fontSize: 12, padding: '2px 6px', borderRadius: 6,
                  transition: 'color 0.15s, background 0.15s',
                }}
                onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--accent)'; e.currentTarget.style.background = 'var(--accent-bg)' }}
                onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-tertiary)'; e.currentTarget.style.background = 'none' }}
              >&#128193;</button>
              <button
                title={t.writing_reference_remove}
                onClick={(e) => { e.stopPropagation(); void handleDeleteFile(artifactPath) }}
                style={{
                  position: 'absolute', right: 8, top: 8,
                  background: 'none', border: 'none', cursor: 'pointer',
                  color: 'var(--text-tertiary)', fontSize: 14, padding: '2px 6px', borderRadius: 6,
                  transition: 'color 0.15s, background 0.15s',
                }}
                onMouseEnter={(e) => { e.currentTarget.style.color = '#dc2626'; e.currentTarget.style.background = '#fef2f2' }}
                onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-tertiary)'; e.currentTarget.style.background = 'none' }}
              >×</button>
            </div>
          ))
        ) : (
          <div style={{ color: 'var(--text-tertiary)' }}>{emptyText}</div>
        )}
      </div>

      {renderPreviewPane()}
    </div>
  )

  return (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 18, height: 'calc(100vh - 100px)', overflow: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{ marginRight: 'auto' }}>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>{t.writing_workspace_title}</h1>
          <div style={{ marginTop: 6, fontSize: 13, color: 'var(--text-secondary)' }}>{t.writing_workspace_desc}</div>
        </div>
        <button style={tabStyle(tab === 'studio')} onClick={() => setTab('studio')}>{t.writing_tab_studio}</button>
        <button style={tabStyle(tab === 'chat')} onClick={() => setTab('chat')}>{t.writing_tab_chat}</button>
        <button style={tabStyle(tab === 'outline')} onClick={() => setTab('outline')}>{t.writing_tab_outline}</button>
        <button style={tabStyle(tab === 'drafts')} onClick={() => setTab('drafts')}>{t.writing_tab_drafts}</button>
        <button style={tabStyle(tab === 'artifacts')} onClick={() => setTab('artifacts')}>{t.writing_tab_artifacts}</button>
        {updateAvailable && (
          <button
            title={`v${updateAvailable}`}
            onClick={() => onNavigate?.('settings')}
            style={{
              marginLeft: 4, padding: '4px 10px', borderRadius: 8, border: 'none',
              background: '#f59e0b', color: '#fff', fontWeight: 700, fontSize: 12,
              cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4,
            }}>
            <span style={{ fontSize: 14 }}>↑</span> v{updateAvailable}
          </button>
        )}
      </div>

      {/* ── 基础设置卡片 ── */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
        gap: 12,
        padding: 18,
        borderRadius: 14,
        background: 'linear-gradient(160deg, rgba(99,102,241,0.06), var(--bg-primary))',
        border: '1px solid var(--border)',
      }}>
        <div style={fieldStyle}>
          <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{t.writing_topic_label}</label>
          <input value={topic} onChange={(event) => setTopic(event.target.value)} placeholder={t.writing_topic_placeholder} style={inputStyle} />
        </div>
        <div style={fieldStyle}>
          <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{t.writing_question_label}</label>
          <input value={question} onChange={(event) => setQuestion(event.target.value)} placeholder={t.writing_question_placeholder} style={inputStyle} />
        </div>
        <div style={fieldStyle}>
          <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{t.writing_language_label}</label>
          <select value={language} onChange={(event) => setLanguage(event.target.value as LanguageOption)} style={inputStyle}>
            <option value="auto">{t.writing_language_auto}</option>
            <option value="zh">{t.writing_language_zh}</option>
            <option value="en">{t.writing_language_en}</option>
          </select>
        </div>
        <div style={fieldStyle}>
          <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{t.writing_paper_type_label}</label>
          <select value={paperType} onChange={(event) => setPaperType(event.target.value as PaperTypeOption)} style={inputStyle}>
            <option value="general">{t.writing_paper_type_general}</option>
            <option value="conference">{t.writing_paper_type_conference}</option>
            <option value="journal">{t.writing_paper_type_journal}</option>
          </select>
        </div>
        <div style={fieldStyle}>
          <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{t.writing_target_words_label}</label>
          <input type="number" min={3000} step={500} value={targetWords} onChange={(event) => setTargetWords(Number(event.target.value) || 15000)} style={inputStyle} />
        </div>
        <div style={fieldStyle}>
          <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{t.writing_docx_style_label}</label>
          <select value={docxStyle} onChange={(event) => setDocxStyle(event.target.value as DocxStyleOption)} style={inputStyle}>
            {docxStyleOptions.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </div>
        <div style={fieldStyle}>
          <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{deckTypeLabel}</label>
          <select value={deckType} onChange={(event) => setDeckType(event.target.value as DeckTypeOption)} style={inputStyle}>
            {deckTypeOptions.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </div>
      </div>

      {/* ── 参考与路径卡片 ── */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '1fr 1fr',
        gap: 12,
        padding: 14,
        borderRadius: 14,
        background: 'var(--bg-primary)',
        border: '1px solid var(--border)',
        alignItems: 'end',
      }}>
        <div style={fieldStyle}>
          <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{t.writing_project_path_label}</label>
          <div style={{ display: 'flex', gap: 8 }}>
            <input value={projectPath} onChange={(event) => setProjectPath(event.target.value)} placeholder={t.writing_project_path_placeholder} style={{ ...inputStyle, flex: 1 }} />
            <button style={{ ...buttonStyle(false), whiteSpace: 'nowrap' as const }} onClick={() => void handleBrowseProject()}>{t.writing_browse_project}</button>
          </div>
        </div>
        <div style={fieldStyle}>
          <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            {t.writing_reference_label}
            {referenceFiles.length > 0 && <span style={{ marginLeft: 6, fontSize: 11, color: 'var(--text-tertiary)' }}>({referenceFiles.length})</span>}
          </label>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', height: 42 }}>
            <button
              style={{
                ...buttonStyle(false),
                height: 42,
                transition: 'transform 0.15s ease, box-shadow 0.15s ease',
                transform: refBtnPulse ? 'scale(0.93)' : 'scale(1)',
                boxShadow: refBtnPulse ? '0 0 0 3px rgba(99,102,241,0.3)' : 'none',
                whiteSpace: 'nowrap' as const,
                flexShrink: 0,
              }}
              onClick={() => void handleAddReferencesAnimated()}
            >{t.writing_reference_browse}</button>
            <div style={{ flex: 1, display: 'flex', flexWrap: 'wrap', gap: 4, maxHeight: 42, overflow: 'auto' }}>
              {referenceFiles.map((f) => (
                <span key={f} style={{ display: 'inline-flex', alignItems: 'center', gap: 3, padding: '2px 8px', borderRadius: 6, background: 'var(--bg-secondary)', fontSize: 11, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const }}>
                  {f.split(/[\\/]/).pop()}
                  <button style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: 12, padding: 0, lineHeight: 1 }} onClick={() => handleRemoveReference(f)}>×</button>
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* ── 导出操作栏 ── */}
      <div style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: 10,
        alignItems: 'flex-end',
      }}>
        <div style={fieldStyle}>
          <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{t.writing_export_artifact_label}</label>
          <select value={exportArtifact} onChange={(event) => setExportArtifact(event.target.value as ExportArtifactOption)} style={inputStyle}>
            {exportArtifactOptions.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
          <button style={buttonStyle(true)} disabled={Boolean(runningLabel)} onClick={() => void runExportDocx()}>
            {t.writing_action_export_docx}
          </button>
          <button style={buttonStyle(false)} disabled={Boolean(runningLabel)} onClick={() => void runExportPptx()}>
            {t.writing_action_export_pptx}
          </button>
        </div>
        <div style={{ marginLeft: 'auto' }}>
          <button
            style={{
              ...buttonStyle(false),
              border: '1px solid #fca5a5',
              background: runningLabel ? '#fff1f2' : 'var(--bg-secondary)',
              color: runningLabel ? '#dc2626' : 'var(--text-tertiary)',
              cursor: runningLabel ? 'pointer' : 'not-allowed',
              opacity: runningLabel ? 1 : 0.5,
            }}
            disabled={!runningLabel}
            onClick={() => void handleCancel()}>
            {canceling ? t.writing_cancelling : t.writing_cancel}
          </button>
        </div>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1.4fr 0.8fr 0.8fr',
          gap: 12,
        }}>
        <div
          style={{
            padding: '14px 16px',
            borderRadius: 14,
            border: `1px solid ${error ? '#fecaca' : 'var(--border)'}`,
            background: error ? '#fef2f2' : 'var(--bg-primary)',
            color: error ? '#dc2626' : 'var(--text-secondary)',
            minHeight: 76,
          }}>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 6 }}>{t.writing_latest_result}</div>
          <div style={{ lineHeight: 1.65 }}>{latestSummary}</div>
          {qualityBadges.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 10 }}>
              {qualityBadges.map((badge) => (
                <span
                  key={badge}
                  style={{
                    padding: '4px 10px',
                    borderRadius: 999,
                    fontSize: 11,
                    fontWeight: 700,
                    background: 'var(--bg-secondary)',
                    color: 'var(--text-secondary)',
                    border: '1px solid var(--border)',
                  }}>
                  {badge}
                </span>
              ))}
            </div>
          )}
        </div>
        <div style={{ padding: '14px 16px', borderRadius: 14, border: '1px solid var(--border)', background: 'var(--bg-primary)' }}>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 6 }}>{t.writing_running_task}</div>
          {runningLabel ? (
            <div>
              <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>{runningLabel}</div>
              {/* Step progress bar */}
              <div style={{ display: 'flex', gap: 3, marginBottom: 8 }}>
                {Array.from({ length: progressTotal }, (_, i) => (
                  <div
                    key={i}
                    style={{
                      flex: 1,
                      height: 6,
                      borderRadius: 3,
                      background: i < progressStep
                        ? '#6366f1'
                        : i === progressStep
                          ? 'linear-gradient(90deg, #6366f1, #818cf8)'
                          : 'var(--bg-secondary)',
                      transition: 'background 0.3s',
                    }}
                  />
                ))}
              </div>
              {/* Current step label */}
              {progressLabel && !canceling && (
                <div style={{ fontSize: 12, color: 'var(--accent)', marginBottom: 4, fontWeight: 600 }}>
                  {progressStep}/{progressTotal} {progressLabel}
                </div>
              )}
              {progressDetail && !canceling && (
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 8, lineHeight: 1.5 }}>
                  {progressDetail}
                </div>
              )}
              {progressEvents.length > 0 && !canceling && (
                <div
                  style={{
                    marginBottom: 10,
                    padding: '10px 12px',
                    borderRadius: 10,
                    background: 'var(--bg-secondary)',
                    border: '1px solid var(--border)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 6,
                  }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-secondary)' }}>
                    {t.writing_task_recent_events}
                  </div>
                  {progressEvents.map((event, index) => (
                    <div key={`${event.timestamp}-${index}`} style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.45 }}>
                      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                        <span style={{ color: 'var(--text-tertiary)', minWidth: 64 }}>
                          {formatProgressTimestamp(event.timestamp) || '--:--:--'}
                        </span>
                        <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{event.label}</span>
                      </div>
                      {event.detail && (
                        <div style={{ marginLeft: 70, color: 'var(--text-tertiary)' }}>{event.detail}</div>
                      )}
                    </div>
                  ))}
                </div>
              )}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 11, color: 'var(--text-tertiary)' }}>
                <span>{canceling ? t.writing_cancelling : t.writing_task_running}</span>
                <span style={{ display: 'flex', gap: 8 }}>
                  {getEstimatedTime(runningLabel) && !canceling && (
                    <span>{t.writing_estimated_time}: {getEstimatedTime(runningLabel)}</span>
                  )}
                  {progressUpdatedAt && !canceling && (
                    <span>{t.writing_task_last_update}: {formatProgressTimestamp(progressUpdatedAt) || '--:--:--'}</span>
                  )}
                  <span>{formatElapsed(elapsedSeconds)}</span>
                </span>
              </div>
            </div>
          ) : (
            <div style={{ fontSize: 14, fontWeight: 700 }}>—</div>
          )}
        </div>
        <div style={{ padding: '14px 16px', borderRadius: 14, border: '1px solid var(--border)', background: 'var(--bg-primary)' }}>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 6 }}>{t.writing_artifact_count}</div>
          <div style={{ fontSize: 18, fontWeight: 800 }}>{workspaceFileCount}</div>
        </div>
      </div>

      <div
        style={{
          flex: 1,
          minHeight: 0,
          borderRadius: 18,
          border: '1px solid var(--border)',
          background: 'var(--bg-primary)',
          overflow: 'hidden',
        }}>
        {tab === 'studio' && (
          <div style={{ height: '100%', padding: 18, display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 14, overflow: 'auto' }}>
            {(workspaceDrafts.length > 0 || workspaceArtifacts.length > 0) && (
              <div
                style={{
                  gridColumn: '1 / -1',
                  borderRadius: 18,
                  border: '1px solid var(--border)',
                  background: 'linear-gradient(180deg, rgba(15,118,110,0.08), transparent 65%), var(--bg-primary)',
                  padding: 18,
                  display: 'grid',
                  gridTemplateColumns: '1fr 1fr',
                  gap: 16,
                }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-tertiary)', letterSpacing: '0.08em', textTransform: 'uppercase' }}>
                    {t.writing_drafts_title}
                  </div>
                  {workspaceDrafts.slice(0, 4).map((path) => (
                    <button
                      key={path}
                      onClick={() => void openPreview(path, 'drafts')}
                      style={{
                        textAlign: 'left',
                        padding: '10px 12px',
                        borderRadius: 12,
                        border: '1px solid var(--border)',
                        background: 'var(--bg-secondary)',
                        color: 'var(--text-primary)',
                        cursor: 'pointer',
                      }}>
                      <div style={{ fontWeight: 700, fontSize: 13 }}>{fileName(path)}</div>
                      <div style={{ marginTop: 4, fontSize: 12, color: 'var(--text-tertiary)' }}>{path}</div>
                    </button>
                  ))}
                  {workspaceDrafts.length === 0 && <div style={{ color: 'var(--text-tertiary)' }}>{t.writing_no_drafts}</div>}
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-tertiary)', letterSpacing: '0.08em', textTransform: 'uppercase' }}>
                    {t.writing_result_paths}
                  </div>
                  {workspaceArtifacts.slice(0, 4).map((path) => (
                    <button
                      key={path}
                      onClick={() => void openPreview(path, 'artifacts')}
                      style={{
                        textAlign: 'left',
                        padding: '10px 12px',
                        borderRadius: 12,
                        border: '1px solid var(--border)',
                        background: 'var(--bg-secondary)',
                        color: 'var(--text-primary)',
                        cursor: 'pointer',
                      }}>
                      <div style={{ fontWeight: 700, fontSize: 13 }}>{fileName(path)}</div>
                      <div style={{ marginTop: 4, fontSize: 12, color: 'var(--text-tertiary)' }}>{path}</div>
                    </button>
                  ))}
                  {workspaceArtifacts.length === 0 && <div style={{ color: 'var(--text-tertiary)' }}>{t.writing_preview_placeholder}</div>}
                </div>
              </div>
            )}

            {cards.map((card) => (
              <div
                key={card.title}
                style={{
                  borderRadius: 18,
                  border: `1px solid ${card.accent}22`,
                  background: `linear-gradient(180deg, ${card.accent}12, transparent 55%), var(--bg-primary)`,
                  padding: 18,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 12,
                  minHeight: 180,
                }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <div style={{ fontSize: 16, fontWeight: 800 }}>{card.title}</div>
                  <div style={{ width: 12, height: 12, borderRadius: 999, background: card.accent }} />
                </div>
                <div style={{ color: 'var(--text-secondary)', lineHeight: 1.7, flex: 1 }}>{card.description}</div>
                <button
                  style={{
                    ...buttonStyle(card.title === t.writing_card_paper_title),
                    width: '100%',
                    background: card.title === t.writing_card_paper_title ? card.accent : `${card.accent}14`,
                    color: card.title === t.writing_card_paper_title ? '#fff' : card.accent,
                    border: `1px solid ${card.accent}55`,
                  }}
                  disabled={Boolean(runningLabel)}
                  onClick={() => void card.onClick()}>
                  {card.action}
                </button>
              </div>
            ))}
          </div>
        )}

        {tab === 'chat' && writingConversation && (
          <LLMChat
            conversationId={writingConversation.id}
            systemPrompt={systemContext}
            provider={settings.default_provider}
            model={settings.default_model}
          />
        )}

        {tab === 'outline' && (
          <div style={{ height: '100%', padding: 18, display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>{t.writing_outline_title}</h2>
              {result?.artifact?.title && (
                <span
                  style={{
                    padding: '4px 12px',
                    borderRadius: 999,
                    fontSize: 12,
                    fontWeight: 700,
                    background: 'rgba(99,102,241,0.12)',
                    color: 'var(--accent)',
                  }}>
                  {result.artifact.title}
                </span>
              )}
            </div>
            {outlineContent ? (
              <div
                style={{
                  flex: 1,
                  overflow: 'auto',
                  padding: 16,
                  borderRadius: 12,
                  background: 'var(--bg-secondary)',
                  whiteSpace: 'pre-wrap',
                  fontSize: 14,
                  lineHeight: 1.7,
                  color: 'var(--text-primary)',
                }}>
                {outlineContent}
              </div>
            ) : (
              <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)' }}>
                {t.writing_no_outline}
              </div>
            )}
          </div>
        )}

        {tab === 'drafts' && renderFileBrowser(t.writing_drafts_title, workspaceDrafts, t.writing_no_drafts, 'drafts')}

        {tab === 'artifacts' && (
          renderFileBrowser(t.writing_result_paths, workspaceArtifacts, t.writing_preview_placeholder, 'artifacts')
        )}
      </div>
    </div>
  )
}

export default Writing
