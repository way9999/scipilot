import { createSlice, createAsyncThunk, type PayloadAction } from '@reduxjs/toolkit'
import type { AppSettings } from '../types/workbench'
import * as api from '../lib/tauri'

interface SettingsState {
  settings: AppSettings
  loading: boolean
}

const initialState: SettingsState = {
  settings: {
    default_provider: 'llm',
    default_model: 'gpt-4o',
    llm_model: 'gpt-4o',
    ollama_model: 'qwen2.5',
    image_gen_model: '',
    default_discipline: 'generic',
    sidecar_auto_start: true,
    language: 'zh' as const,
    api_base_urls: {},
    api_keys: {},
    agent_enabled: false,
    agent_type: 'claude_code' as const,
    agent_path: '',
    agent_max_turns: 10,
    agent_timeout_secs: 300,
    agent_auto_fix: true,
    agent_auto_supplement: true,
  },
  loading: false,
}

export const fetchSettings = createAsyncThunk('settings/fetch', async () => {
  return api.getSettings()
})

export const saveSettings = createAsyncThunk(
  'settings/save',
  async (settings: AppSettings) => {
    await api.updateSettings(settings)
    return settings
  }
)

const settingsSlice = createSlice({
  name: 'settings',
  initialState,
  reducers: {
    setSettings(state, action: PayloadAction<Partial<AppSettings>>) {
      state.settings = { ...state.settings, ...action.payload }
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(fetchSettings.fulfilled, (state, action) => {
        state.settings = action.payload
      })
      .addCase(saveSettings.fulfilled, (state, action) => {
        state.settings = action.payload
      })
  },
})

export const { setSettings } = settingsSlice.actions
export default settingsSlice.reducer
