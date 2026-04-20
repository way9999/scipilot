import { getVersion } from '@tauri-apps/api/app'
import { invoke } from '@tauri-apps/api/core'
import * as updater from '@tauri-apps/plugin-updater'
import type {
  SidecarResponse,
  AppSettings,
  ExportDocxRequest,
  ExportPptxRequest,
  WritingTaskStartResult,
  WritingTaskStatusResult,
} from '../types/workbench'

// Research commands
export async function searchPapers(
  query: string,
  discipline: string,
  limit: number,
  download: boolean
): Promise<SidecarResponse> {
  return invoke('search_papers', { query, discipline, limit, download })
}

export async function downloadPaper(recordId: string): Promise<SidecarResponse> {
  return invoke('download_paper', { recordId })
}

export async function refreshWorkbench(): Promise<SidecarResponse> {
  return invoke('refresh_workbench')
}

export async function getPapers(
  discipline?: string,
  source?: string
): Promise<SidecarResponse> {
  return invoke('get_papers', { discipline, source })
}

export async function getDashboard(): Promise<SidecarResponse> {
  return invoke('get_dashboard')
}

export async function verifyPaper(
  title: string,
  authors: string[]
): Promise<SidecarResponse> {
  return invoke('verify_paper', { title, authors })
}

export async function sidecarHealth(): Promise<SidecarResponse> {
  return invoke('sidecar_health')
}

export async function getSidecarUrl(): Promise<string> {
  return invoke('get_sidecar_url')
}

export async function getLlmConfig(provider: string): Promise<{ api_key: string; base_url: string; model: string }> {
  return invoke('get_llm_config', { provider })
}

export async function setApiKey(
  provider: string,
  key: string
): Promise<void> {
  return invoke('set_api_key', { provider, key })
}

export async function getProviders(): Promise<string[]> {
  return invoke('get_providers')
}

export async function testLlmConnection(
  provider: string,
  model: string
): Promise<{ success: boolean; message: string }> {
  return invoke('test_llm_connection', { provider, model })
}

// Settings commands
export async function getSettings(): Promise<AppSettings> {
  return invoke('get_settings')
}

export async function updateSettings(settings: AppSettings): Promise<void> {
  return invoke('update_settings', { settings })
}

export async function getProjectRoot(): Promise<string> {
  return invoke('get_project_root')
}

export async function pickDirectory(initialPath?: string): Promise<string | null> {
  return invoke('pick_directory', { initialPath: initialPath || null })
}

export async function pickReferenceFiles(
  initialPath?: string
): Promise<string[] | null> {
  return invoke('pick_files', { initialPath: initialPath || null })
}

export async function detectAgentCli(
  agentType: string
): Promise<string | null> {
  return invoke('detect_agent_cli', { agentType })
}

// Batch operations
export async function batchDownload(recordIds: string[]): Promise<SidecarResponse> {
  return invoke('batch_download', { recordIds })
}

export async function batchVerify(recordIds: string[]): Promise<SidecarResponse> {
  return invoke('batch_verify', { recordIds })
}

export async function getRecommendations(): Promise<SidecarResponse> {
  return invoke('get_recommendations')
}

// Landscape analysis
export async function landscapeAnalyze(
  topic: string,
  discipline: string,
  limit: number
): Promise<SidecarResponse> {
  return invoke('landscape_analyze', { topic, discipline, limit })
}

export async function readProjectFile(relPath: string): Promise<string> {
  return invoke('read_project_file', { relPath })
}

export async function readProjectFileBinary(relPath: string): Promise<string> {
  return invoke('read_project_file_binary', { relPath })
}

export async function openFileInSystem(relPath: string): Promise<void> {
  return invoke('open_file_in_system', { relPath })
}

export async function showInFileManager(relPath: string): Promise<void> {
  return invoke('show_in_file_manager', { relPath })
}

export async function deleteProjectFile(relPath: string): Promise<void> {
  return invoke('delete_project_file', { relPath })
}

export async function startGeneratePaperDraft(
  topic: string,
  language: string,
  paperType: string,
  targetWords?: number,
  referenceFiles?: string[]
): Promise<SidecarResponse<WritingTaskStartResult>> {
  return invoke('start_generate_paper_draft', { topic, language, paperType, targetWords, referenceFiles })
}

export async function startGeneratePaperFromProject(
  sourceProject: string,
  topic: string,
  language: string,
  paperType: string,
  targetWords?: number,
  referenceFiles?: string[]
): Promise<SidecarResponse<WritingTaskStartResult>> {
  return invoke('start_generate_paper_from_project', {
    sourceProject,
    topic,
    language,
    paperType,
    targetWords,
    referenceFiles,
  })
}

export async function startGenerateProposal(
  topic: string,
  language: string
): Promise<SidecarResponse<WritingTaskStartResult>> {
  return invoke('start_generate_proposal', { topic, language })
}

export async function startGeneratePresentation(
  topic: string,
  language: string,
  deckType: string
): Promise<SidecarResponse<WritingTaskStartResult>> {
  return invoke('start_generate_presentation', { topic, language, deckType })
}

export async function startGenerateLiteratureReview(
  topic: string,
  language: string
): Promise<SidecarResponse<WritingTaskStartResult>> {
  return invoke('start_generate_literature_review', { topic, language })
}

export async function startRefineDraft(
  source: string,
  language: string
): Promise<SidecarResponse<WritingTaskStartResult>> {
  return invoke('start_refine_draft', { source, language })
}

export async function startAnswerResearchQuestion(
  question: string,
  language: string
): Promise<SidecarResponse<WritingTaskStartResult>> {
  return invoke('start_answer_research_question', { question, language })
}

export async function startExportDocx(
  payload: ExportDocxRequest
): Promise<SidecarResponse<WritingTaskStartResult>> {
  return invoke('start_export_docx', { ...payload })
}

export async function startExportPptx(
  payload: ExportPptxRequest
): Promise<SidecarResponse<WritingTaskStartResult>> {
  return invoke('start_export_pptx', { ...payload })
}

export async function getWritingTaskStatus(
  taskId: string
): Promise<SidecarResponse<WritingTaskStatusResult>> {
  return invoke('get_writing_task_status', { taskId })
}

export async function cancelWritingTask(
  taskId: string
): Promise<SidecarResponse<WritingTaskStatusResult>> {
  return invoke('cancel_writing_task', { taskId })
}

// Updater
export interface UpdateInfo {
  available: boolean
  version?: string
  date?: string
  body?: string
}

export async function checkForUpdates(): Promise<UpdateInfo> {
  try {
    const update = await updater.check()
    if (update) {
      return {
        available: true,
        version: update.version,
        date: update.date,
        body: update.body,
      }
    }
    return { available: false }
  } catch {
    return { available: false }
  }
}

export interface UpdateProgress {
  downloaded: number
  total?: number
}

export async function getAppVersion(): Promise<string> {
  return getVersion()
}

export async function downloadAndInstallUpdate(
  onProgress?: (progress: UpdateProgress) => void
): Promise<boolean> {
  try {
    const update = await updater.check()
    if (update) {
      let downloaded = 0
      let total: number | undefined
      await update.downloadAndInstall((event) => {
        if (event.event === 'Started') {
          downloaded = 0
          total = event.data.contentLength
          onProgress?.({ downloaded, total })
        } else if (event.event === 'Progress') {
          downloaded += event.data.chunkLength
          onProgress?.({ downloaded, total })
        }
      })
      return true
    }
    return false
  } catch {
    return false
  }
}

// License
export interface LicenseStatus {
  valid: boolean
  tier: 'Free' | 'Student' | 'Pro'
}

export async function activateLicense(key: string): Promise<LicenseStatus> {
  return invoke('activate_license', { key })
}

export async function getLicenseStatus(): Promise<LicenseStatus> {
  return invoke('get_license_status')
}

export async function deactivateLicense(): Promise<void> {
  return invoke('deactivate_license')
}
