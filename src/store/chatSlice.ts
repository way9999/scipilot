import { createSlice, type PayloadAction } from '@reduxjs/toolkit'
import type { ChatMessage, Conversation } from '../types/workbench'

interface ChatState {
  conversations: Conversation[]
  activeConversationId: string | null
  streaming: boolean
  streamContent: string
}

const STORAGE_KEY = 'scipilot_conversations'

function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function saveConversations(conversations: Conversation[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations))
  } catch { /* ignore quota errors */ }
}

const initialState: ChatState = {
  conversations: loadConversations(),
  activeConversationId: null,
  streaming: false,
  streamContent: '',
}

const chatSlice = createSlice({
  name: 'chat',
  initialState,
  reducers: {
    createConversation(state, action: PayloadAction<{ assistantId: string; title?: string }>) {
      const conv: Conversation = {
        id: `conv_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
        assistantId: action.payload.assistantId,
        title: action.payload.title || '新对话',
        messages: [],
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      }
      state.conversations.unshift(conv)
      state.activeConversationId = conv.id
      saveConversations(state.conversations)
    },

    setActiveConversation(state, action: PayloadAction<string | null>) {
      state.activeConversationId = action.payload
      state.streaming = false
      state.streamContent = ''
    },

    addMessage(state, action: PayloadAction<{ conversationId: string; message: ChatMessage }>) {
      const conv = state.conversations.find((c) => c.id === action.payload.conversationId)
      if (conv) {
        conv.messages.push(action.payload.message)
        conv.updatedAt = new Date().toISOString()
        // Auto-title from first user message
        if (conv.messages.length === 1 && action.payload.message.role === 'user') {
          const text = action.payload.message.content
          conv.title = text.length > 40 ? text.slice(0, 40) + '...' : text
        }
        saveConversations(state.conversations)
      }
    },

    startStreaming(state) {
      state.streaming = true
      state.streamContent = ''
    },

    appendStreamChunk(state, action: PayloadAction<string>) {
      state.streamContent += action.payload
    },

    finishStreaming(state, action: PayloadAction<{ conversationId: string; content: string }>) {
      const { conversationId, content } = action.payload
      const conv = state.conversations.find((c) => c.id === conversationId)
      if (conv && content) {
        conv.messages.push({ role: 'assistant', content })
        conv.updatedAt = new Date().toISOString()
        saveConversations(state.conversations)
      }
      state.streaming = false
      state.streamContent = ''
    },

    deleteConversation(state, action: PayloadAction<string>) {
      state.conversations = state.conversations.filter((c) => c.id !== action.payload)
      if (state.activeConversationId === action.payload) {
        state.activeConversationId = state.conversations[0]?.id ?? null
      }
      saveConversations(state.conversations)
    },
  },
})

export const {
  createConversation,
  setActiveConversation,
  addMessage,
  startStreaming,
  appendStreamChunk,
  finishStreaming,
  deleteConversation,
} = chatSlice.actions

export default chatSlice.reducer
