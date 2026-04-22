export interface GroupChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  model?: string
  timestamp: string
}

export interface GroupChatConversation {
  id: string
  title: string
  messages: GroupChatMessage[]
  mode: 'parallel' | 'debate' | 'relay' | 'free'
  participants: ModelParticipant[]
  createdAt: string
  updatedAt: string
}

export interface ModelParticipant {
  id: string
  name: string
  provider: string
  model: string
  color: string
  avatar: string
  role?: string
  enabled: boolean
}

export interface ConsensusResult {
  type: 'consensus' | 'divergence' | 'partial'
  summary: string
  points: {
    text: string
    agreement: number
    models: string[]
  }[]
}
