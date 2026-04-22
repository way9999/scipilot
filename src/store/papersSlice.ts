import { createSlice, createAsyncThunk } from '@reduxjs/toolkit'
import type { ResearchWorkbenchPaper } from '../types/workbench'
import * as api from '../lib/tauri'

interface PapersState {
  items: ResearchWorkbenchPaper[]
  loading: boolean
  error: string | null
  searchLoading: boolean
  downloadingId: string | null
  crawlingId: string | null
  batchLoading: boolean
  searchMeta: {
    query?: string
    result_count?: number
    downloaded_count?: number
    crawled_count?: number
    crawl_failed?: number
  } | null
}

const initialState: PapersState = {
  items: [],
  loading: false,
  error: null,
  searchLoading: false,
  downloadingId: null,
  crawlingId: null,
  batchLoading: false,
  searchMeta: null,
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
    return resp.data as {
      results: ResearchWorkbenchPaper[]
      indexed_count: number
      result_count: number
      downloaded_count: number
      crawled_count: number
      crawl_failed: number
      query: string
    }
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

export const crawlPaper = createAsyncThunk(
  'papers/crawl',
  async (recordId: string) => {
    const resp = await api.crawlPaper(recordId)
    if (!resp.success) throw new Error(resp.error || 'Crawl failed')
    return resp.data as { paper: ResearchWorkbenchPaper }
  }
)

export const batchCrawlPapers = createAsyncThunk(
  'papers/batchCrawl',
  async (recordIds: string[]) => {
    const resp = await api.batchCrawl(recordIds)
    if (!resp.success) throw new Error(resp.error || 'Batch crawl failed')
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
      .addCase(searchPapers.fulfilled, (state, action) => {
        state.searchLoading = false
        const d = action.payload
        if (d.results) {
          state.items = d.results
        }
        state.searchMeta = {
          query: d.query,
          result_count: d.result_count,
          downloaded_count: d.downloaded_count ?? 0,
          crawled_count: d.crawled_count ?? 0,
          crawl_failed: d.crawl_failed ?? 0,
        }
      })
      .addCase(searchPapers.rejected, (state, action) => {
        state.searchLoading = false
        state.error = action.error.message || 'Search failed'
      })
      .addCase(downloadPaper.pending, (state, action) => {
        state.downloadingId = action.meta.arg
      })
      .addCase(downloadPaper.fulfilled, (state, action) => {
        state.downloadingId = null
        const updated = action.payload?.paper
        if (updated?.record_id) {
          const idx = state.items.findIndex(p => p.record_id === updated.record_id)
          if (idx >= 0) state.items[idx] = { ...state.items[idx], ...updated }
        }
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
      .addCase(crawlPaper.pending, (state, action) => {
        state.crawlingId = action.meta.arg
      })
      .addCase(crawlPaper.fulfilled, (state, action) => {
        state.crawlingId = null
        const updated = action.payload?.paper
        if (updated?.record_id) {
          const idx = state.items.findIndex(p => p.record_id === updated.record_id)
          if (idx >= 0) state.items[idx] = { ...state.items[idx], ...updated }
        }
      })
      .addCase(crawlPaper.rejected, (state, action) => {
        state.crawlingId = null
        state.error = action.error.message || 'Crawl failed'
      })
      .addCase(batchCrawlPapers.pending, (state) => {
        state.batchLoading = true
      })
      .addCase(batchCrawlPapers.fulfilled, (state) => {
        state.batchLoading = false
      })
      .addCase(batchCrawlPapers.rejected, (state, action) => {
        state.batchLoading = false
        state.error = action.error.message || 'Batch crawl failed'
      })
  },
})

export default papersSlice.reducer
