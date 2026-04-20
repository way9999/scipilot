import { createSlice, createAsyncThunk } from '@reduxjs/toolkit'
import type { ResearchProjectState, ResearchRoute, ResearchWorkbenchSummary, Recommendation } from '../types/workbench'
import * as api from '../lib/tauri'

interface ResearchState {
  projectState: ResearchProjectState | null
  route: ResearchRoute | null
  summary: ResearchWorkbenchSummary | null
  loading: boolean
  error: string | null
  sidecarOnline: boolean
  recommendations: Recommendation[]
}

const initialState: ResearchState = {
  projectState: null,
  route: null,
  summary: null,
  loading: false,
  error: null,
  sidecarOnline: false,
  recommendations: [],
}

export const checkSidecarHealth = createAsyncThunk(
  'research/checkHealth',
  async () => {
    const resp = await api.sidecarHealth()
    return resp.success
  }
)

export const fetchDashboard = createAsyncThunk(
  'research/fetchDashboard',
  async () => {
    const resp = await api.getDashboard()
    if (!resp.success) throw new Error(resp.error || 'Failed to fetch dashboard')
    return resp.data as {
      state: ResearchProjectState
      route: ResearchRoute
      summary: ResearchWorkbenchSummary
    }
  }
)

export const refreshWorkbench = createAsyncThunk(
  'research/refresh',
  async () => {
    const resp = await api.refreshWorkbench()
    if (!resp.success) throw new Error(resp.error || 'Refresh failed')
    return resp.data
  }
)

export const fetchRecommendations = createAsyncThunk(
  'research/fetchRecommendations',
  async () => {
    const resp = await api.getRecommendations()
    if (!resp.success) throw new Error(resp.error || 'Failed to fetch recommendations')
    return (resp.data as { recommendations: Recommendation[] }).recommendations
  }
)

const researchSlice = createSlice({
  name: 'research',
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder
      .addCase(checkSidecarHealth.fulfilled, (state, action) => {
        state.sidecarOnline = action.payload
      })
      .addCase(checkSidecarHealth.rejected, (state) => {
        state.sidecarOnline = false
      })
      .addCase(fetchDashboard.pending, (state) => {
        state.loading = true
        state.error = null
      })
      .addCase(fetchDashboard.fulfilled, (state, action) => {
        state.loading = false
        const data = action.payload
        if (data) {
          state.projectState = data.state
          state.route = data.route
          state.summary = data.summary
        }
      })
      .addCase(fetchDashboard.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Failed to fetch dashboard'
      })
      .addCase(refreshWorkbench.pending, (state) => {
        state.loading = true
      })
      .addCase(refreshWorkbench.fulfilled, (state) => {
        state.loading = false
      })
      .addCase(refreshWorkbench.rejected, (state, action) => {
        state.loading = false
        state.error = action.error.message || 'Refresh failed'
      })
      .addCase(fetchRecommendations.fulfilled, (state, action) => {
        state.recommendations = action.payload
      })
  },
})

export default researchSlice.reducer
