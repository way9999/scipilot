import { createSlice, createAsyncThunk } from '@reduxjs/toolkit'
import type { ResearchWorkbenchPaper } from '../types/workbench'
import * as api from '../lib/tauri'

interface PapersState {
  items: ResearchWorkbenchPaper[]
  loading: boolean
  error: string | null
  searchLoading: boolean
  downloadingId: string | null
  batchLoading: boolean
}

const initialState: PapersState = {
  items: [],
  loading: false,
  error: null,
  searchLoading: false,
  downloadingId: null,
  batchLoading: false,
}

export const fetchPapers = createAsyncThunk(
  'papers/fetch',
  async (params?: { discipline?: string; source?: string }) => {
    const resp = await api.getPapers(params?.discipline, params?.source)
    if (!resp.success) throw new Error(resp.error || 'Failed to fetch papers')
    return (resp.data || []) as ResearchWorkbenchPaper[]
  }
)

export const searchPapers = createAsyncThunk(
  'papers/search',
  async (params: {
    query: string
    discipline: string
    limit: number
    download: boolean
  }) => {
    const resp = await api.searchPapers(
      params.query,
      params.discipline,
      params.limit,
      params.download
    )
    if (!resp.success) throw new Error(resp.error || 'Search failed')
    return resp.data as { results: ResearchWorkbenchPaper[]; indexed_count: number }
  }
)

export const downloadPaper = createAsyncThunk(
  'papers/download',
  async (recordId: string) => {
    const resp = await api.downloadPaper(recordId)
    if (!resp.success) throw new Error(resp.error || 'Download failed')
    return resp.data as { paper: ResearchWorkbenchPaper }
  }
)

export const batchDownloadPapers = createAsyncThunk(
  'papers/batchDownload',
  async (recordIds: string[]) => {
    const resp = await api.batchDownload(recordIds)
    if (!resp.success) throw new Error(resp.error || 'Batch download failed')
    return resp.data
  }
)

export const batchVerifyPapers = createAsyncThunk(
  'papers/batchVerify',
  async (recordIds: string[]) => {
    const resp = await api.batchVerify(recordIds)
    if (!resp.success) throw new Error(resp.error || 'Batch verify failed')
    return resp.data
  }
)

const papersSlice = createSlice({
  name: 'papers',
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder
      .addCase(fetchPapers.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(fetchPapers.fulfilled, (state, action) => {
        state.loading = false
        state.items = action.payload
      })
      .addCase(fetchPapers.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Failed to fetch papers'
      })
      .addCase(searchPapers.pending, (state) => {
        state.searchLoading = true
        state.error = null
      })
      .addCase(searchPapers.fulfilled, (state) => {
        state.searchLoading = false
      })
      .addCase(searchPapers.rejected, (state, action) => {
        state.searchLoading = false
        state.error = action.error.message || 'Search failed'
      })
      .addCase(downloadPaper.pending, (state, action) => {
        state.downloadingId = action.meta.arg
      })
      .addCase(downloadPaper.fulfilled, (state) => {
        state.downloadingId = null
      })
      .addCase(downloadPaper.rejected, (state, action) => {
        state.downloadingId = null
        state.error = action.error.message || 'Download failed'
      })
      .addCase(batchDownloadPapers.pending, (state) => {
        state.batchLoading = true
      })
      .addCase(batchDownloadPapers.fulfilled, (state) => {
        state.batchLoading = false
      })
      .addCase(batchDownloadPapers.rejected, (state, action) => {
        state.batchLoading = false
        state.error = action.error.message || 'Batch download failed'
      })
      .addCase(batchVerifyPapers.pending, (state) => {
        state.batchLoading = true
      })
      .addCase(batchVerifyPapers.fulfilled, (state) => {
        state.batchLoading = false
      })
      .addCase(batchVerifyPapers.rejected, (state, action) => {
        state.batchLoading = false
        state.error = action.error.message || 'Batch verify failed'
      })
  },
})

export default papersSlice.reducer
