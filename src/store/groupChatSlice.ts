import { createSlice, type PayloadAction } from '@reduxjs/toolkit'
import type { GroupChatConversation, GroupChatMessage, ModelParticipant, ConsensusResult } from '../types/groupChat'

const MODEL_CONFIGS = [
  {
    id: 'model-a',
    name: '模型 A',
    provider: 'llm',
    model: '',  // Will be populated from settings
    color: '#D97706',
    avatar: '🟦',
    role: 'analyst' as const,
    enabled: true,
  },
  {
    id: 'model-b',
    name: '模型 B',
    provider: 'llm',
    model: '',  // Will be populated from settings
    color: '#10A37F',
    avatar: '🟩',
    role: 'creative' as const,
    enabled: false,
  },
  {
    id: 'model-c',
    name: '模型 C',
    provider: 'llm',
    model: '',
    color: '#4285F4',
    avatar: '🟨',
    role: 'researcher' as const,
    enabled: false,
  },
  {
    id: 'ollama',
    name: 'Ollama',
    provider: 'ollama',
    model: 'qwen2.5',
    color: '#6366F1',
    avatar: '🟪',
    role: 'local' as const,
    enabled: false,
  },
]

interface GroupChatState {
  conversations: GroupChatConversation[]
  activeConversationId: string | null
  participants: ModelParticipant[]
  mode: 'parallel' | 'debate' | 'relay' | 'free'
  streaming: Record<string, string> // modelId → partial content
  consensus: ConsensusResult | null
}

const STORAGE_KEY = 'scipilot_group_conversations'

function loadConversations(): GroupChatConversation[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function saveConversations(conversations: GroupChatConversation[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations))
  } catch { /* ignore */ }
}

const initialState: GroupChatState = {
  conversations: loadConversations(),
  activeConversationId: null,
  participants: MODEL_CONFIGS,
  mode: 'parallel',
  streaming: {},
  consensus: null,
}

const groupChatSlice = createSlice({
  name: 'groupChat',
  initialState,
  reducers: {
    createGroupConversation(state) {
      const conv: GroupChatConversation = {
        id: `gc_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
        title: '群聊',
        messages: [],
        mode: state.mode,
        participants: state.participants.filter((p) => p.enabled),
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      }
      state.conversations.unshift(conv)
      state.activeConversationId = conv.id
      saveConversations(state.conversations)
    },

    setActiveGroupConversation(state, action: PayloadAction<string | null>) {
      state.activeConversationId = action.payload
      state.streaming = {}
      state.consensus = null
    },

    addGroupMessage(state, action: PayloadAction<{ conversationId: string; message: GroupChatMessage }>) {
      const conv = state.conversations.find((c) => c.id === action.payload.conversationId)
      if (conv) {
        conv.messages.push(action.payload.message)
        conv.updatedAt = new Date().toISOString()
        if (conv.messages.length === 1 && action.payload.message.role === 'user') {
          const text = action.payload.message.content
          conv.title = text.length > 40 ? text.slice(0, 40) + '...' : text
        }
        saveConversations(state.conversations)
      }
    },

    startModelStream(state, action: PayloadAction<{ modelId: string }>) {
      state.streaming[action.payload.modelId] = ''
    },

    appendModelStream(state, action: PayloadAction<{ modelId: string; chunk: string }>) {
      state.streaming[action.payload.modelId] = (state.streaming[action.payload.modelId] || '') + action.payload.chunk
    },

    finishModelStream(state, action: PayloadAction<{ conversationId: string; modelId: string; content: string }>) {
      const { conversationId, modelId, content } = action.payload
      const conv = state.conversations.find((c) => c.id === conversationId)
      if (conv && content) {
        conv.messages.push({
          id: `msg_${Date.now()}_${modelId}`,
          role: 'assistant',
          content,
          model: modelId,
          timestamp: new Date().toISOString(),
        })
        conv.updatedAt = new Date().toISOString()
        saveConversations(state.conversations)
      }
      delete state.streaming[modelId]
    },

    clearAllStreams(state) {
      state.streaming = {}
    },

    setMode(state, action: PayloadAction<'parallel' | 'debate' | 'relay' | 'free'>) {
      state.mode = action.payload
    },

    toggleParticipant(state, action: PayloadAction<string>) {
      const p = state.participants.find((p) => p.id === action.payload)
      if (p) {
        p.enabled = !p.enabled
      }
    },

    updateParticipant(state, action: PayloadAction<{ id: string; changes: Partial<ModelParticipant> }>) {
      const p = state.participants.find((p) => p.id === action.payload.id)
      if (p) {
        Object.assign(p, action.payload.changes)
      }
    },

    setConsensus(state, action: PayloadAction<ConsensusResult | null>) {
      state.consensus = action.payload
    },

    deleteGroupConversation(state, action: PayloadAction<string>) {
      state.conversations = state.conversations.filter((c) => c.id !== action.payload)
      if (state.activeConversationId === action.payload) {
        state.activeConversationId = state.conversations[0]?.id ?? null
      }
      saveConversations(state.conversations)
    },
  },
})

export const {
  createGroupConversation,
  setActiveGroupConversation,
  addGroupMessage,
  startModelStream,
  appendModelStream,
  finishModelStream,
  clearAllStreams,
  setMode,
  toggleParticipant,
  updateParticipant,
  setConsensus,
  deleteGroupConversation,
} = groupChatSlice.actions

export default groupChatSlice.reducer
