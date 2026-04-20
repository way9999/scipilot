import { type FC, useMemo, useState } from 'react'
import { useAppDispatch, useAppSelector } from '../store'
import { createConversation, deleteConversation, setActiveConversation } from '../store/chatSlice'
import { ASSISTANT_PRESETS } from '../data/assistants'
import type { AssistantPreset } from '../types/workbench'
import LLMChat from '../components/LLMChat'
import { useT } from '../i18n/context'

const Assistants: FC = () => {
  const dispatch = useAppDispatch()
  const { conversations, activeConversationId } = useAppSelector((s) => s.chat)
  const { settings } = useAppSelector((s) => s.settings)
  const [selectedAssistantId, setSelectedAssistantId] = useState<string | null>(null)
  const t = useT()

  const activeConv = conversations.find((c) => c.id === activeConversationId)
  const activeAssistant = ASSISTANT_PRESETS.find(
    (assistant) => assistant.id === (activeConv?.assistantId ?? selectedAssistantId)
  )

  const presetL10n: Record<string, { name: string; desc: string }> = {
    general: { name: t.preset_general, desc: t.preset_general_desc },
    focus: { name: t.preset_focus, desc: t.preset_focus_desc },
    'lit-research': { name: t.preset_lit_research, desc: t.preset_lit_research_desc },
    proposal: { name: t.preset_proposal, desc: t.preset_proposal_desc },
    review: { name: t.preset_review, desc: t.preset_review_desc },
    paper: { name: t.preset_paper, desc: t.preset_paper_desc },
  }

  const getName = (assistant: AssistantPreset) => presetL10n[assistant.id]?.name ?? assistant.name
  const getDesc = (assistant: AssistantPreset) => presetL10n[assistant.id]?.desc ?? assistant.description

  const conversationsByAssistant = useMemo(() => {
    const grouped: Record<string, typeof conversations> = {}
    for (const conversation of conversations) {
      ;(grouped[conversation.assistantId] ??= []).push(conversation)
    }
    return grouped
  }, [conversations])

  const handleNewChat = (assistantId: string) => {
    const preset = ASSISTANT_PRESETS.find((assistant) => assistant.id === assistantId)
    dispatch(
      createConversation({
        assistantId,
        title: preset ? `${getName(preset)} · ${t.chat_new_conversation}` : t.chat_new_conversation,
      })
    )
    setSelectedAssistantId(assistantId)
  }

  const handleSelectConversation = (conversationId: string) => {
    dispatch(setActiveConversation(conversationId))
    const conversation = conversations.find((item) => item.id === conversationId)
    if (conversation) {
      setSelectedAssistantId(conversation.assistantId)
    }
  }

  const handleDeleteConversation = (event: React.MouseEvent, conversationId: string) => {
    event.stopPropagation()
    dispatch(deleteConversation(conversationId))
  }

  const showPicker = !activeConversationId

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      <div
        style={{
          width: 260,
          borderRight: '1px solid var(--border)',
          background: 'var(--bg-primary)',
          display: 'flex',
          flexDirection: 'column',
          flexShrink: 0,
        }}>
        <div style={{ padding: '16px 16px 12px', borderBottom: '1px solid var(--border)' }}>
          <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 10 }}>{t.assist_sidebar_title}</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {ASSISTANT_PRESETS.map((assistant) => (
              <button
                key={assistant.id}
                onClick={() => {
                  setSelectedAssistantId(assistant.id)
                  dispatch(setActiveConversation(null))
                }}
                style={{
                  padding: '5px 10px',
                  borderRadius: 8,
                  border: `1px solid ${selectedAssistantId === assistant.id && showPicker ? `${assistant.color}40` : 'var(--border)'}`,
                  background: selectedAssistantId === assistant.id && showPicker ? `${assistant.color}14` : 'transparent',
                  color: selectedAssistantId === assistant.id && showPicker ? assistant.color : 'var(--text-secondary)',
                  fontSize: 12,
                  fontWeight: 500,
                  cursor: 'pointer',
                  whiteSpace: 'nowrap',
                  transition: 'all 0.15s',
                }}
                title={getDesc(assistant)}>
                {assistant.icon} {getName(assistant)}
              </button>
            ))}
          </div>
        </div>

        <div style={{ padding: '10px 12px 4px' }}>
          <button
            onClick={() => handleNewChat(selectedAssistantId || 'general')}
            style={{
              width: '100%',
              padding: '9px 12px',
              borderRadius: 10,
              border: '1px dashed var(--border)',
              background: 'transparent',
              color: 'var(--accent)',
              fontSize: 13,
              fontWeight: 600,
              cursor: 'pointer',
            }}>
            {t.assist_new_chat}
          </button>
        </div>

        <div style={{ flex: 1, overflow: 'auto', padding: '8px 8px' }}>
          {ASSISTANT_PRESETS.map((assistant) => {
            const assistantConversations = conversationsByAssistant[assistant.id]
            if (!assistantConversations?.length) {
              return null
            }
            return (
              <div key={assistant.id} style={{ marginBottom: 8 }}>
                <div
                  style={{
                    fontSize: 11,
                    fontWeight: 600,
                    color: assistant.color,
                    padding: '4px 8px',
                    textTransform: 'uppercase',
                    letterSpacing: '0.06em',
                  }}>
                  {assistant.icon} {getName(assistant)}
                </div>

                {assistantConversations.map((conversation) => (
                  <div
                    key={conversation.id}
                    onClick={() => handleSelectConversation(conversation.id)}
                    style={{
                      padding: '8px 10px',
                      borderRadius: 8,
                      cursor: 'pointer',
                      background: conversation.id === activeConversationId ? 'var(--accent-bg)' : 'transparent',
                      borderLeft: conversation.id === activeConversationId ? '3px solid var(--accent)' : '3px solid transparent',
                      transition: 'all 0.12s',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: 6,
                    }}>
                    <div
                      style={{
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        fontSize: 13,
                        color: conversation.id === activeConversationId ? 'var(--accent)' : 'var(--text-primary)',
                        fontWeight: conversation.id === activeConversationId ? 600 : 400,
                      }}>
                      {conversation.title}
                    </div>
                    <button
                      onClick={(event) => handleDeleteConversation(event, conversation.id)}
                      style={{
                        background: 'none',
                        border: 'none',
                        color: 'var(--text-tertiary)',
                        cursor: 'pointer',
                        fontSize: 14,
                        padding: '0 2px',
                        flexShrink: 0,
                        opacity: 0.5,
                      }}
                      title={t.chat_delete}>
                      ×
                    </button>
                  </div>
                ))}
              </div>
            )
          })}

          {conversations.length === 0 && (
            <div style={{ color: 'var(--text-tertiary)', fontSize: 12, textAlign: 'center', padding: '24px 8px' }}>
              {t.assist_no_conv}
              <br />
              {t.assist_no_conv_hint}
            </div>
          )}
        </div>
      </div>

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {showPicker ? (
          <AssistantPicker
            assistants={ASSISTANT_PRESETS}
            selectedId={selectedAssistantId}
            onSelect={(id) => setSelectedAssistantId(id)}
            onStartChat={(id) => handleNewChat(id)}
            getName={getName}
            getDesc={getDesc}
          />
        ) : activeConv && activeAssistant ? (
          <>
            <div
              style={{
                padding: '12px 20px',
                borderBottom: '1px solid var(--border)',
                background: 'var(--bg-primary)',
                display: 'flex',
                alignItems: 'center',
                gap: 10,
              }}>
              <span style={{ fontSize: 20 }}>{activeAssistant.icon}</span>
              <div>
                <div style={{ fontWeight: 600, fontSize: 14 }}>{getName(activeAssistant)}</div>
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
                  {activeConv.messages.length} {t.assist_messages}
                </div>
              </div>
            </div>
            <div style={{ flex: 1, minHeight: 0 }}>
              <LLMChat
                conversationId={activeConv.id}
                systemPrompt={activeAssistant.systemPrompt}
                provider={settings.default_provider}
                model={settings.default_model}
                enableTools={activeAssistant.id === 'lit-research'}
              />
            </div>
          </>
        ) : (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)' }}>
            {t.assist_select_hint}
          </div>
        )}
      </div>
    </div>
  )
}

const AssistantPicker: FC<{
  assistants: AssistantPreset[]
  selectedId: string | null
  onSelect: (id: string) => void
  onStartChat: (id: string) => void
  getName: (assistant: AssistantPreset) => string
  getDesc: (assistant: AssistantPreset) => string
}> = ({ assistants, selectedId, onSelect, onStartChat, getName, getDesc }) => {
  const t = useT()
  const selected = assistants.find((assistant) => assistant.id === selectedId) || assistants[0]

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: 32, display: 'flex', flexDirection: 'column', gap: 24 }}>
      <div>
        <h1 style={{ margin: '0 0 6px', fontSize: 24, fontWeight: 700 }}>{t.assist_title}</h1>
        <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: 14 }}>{t.assist_subtitle}</p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 14 }}>
        {assistants.map((assistant) => {
          const isActive = assistant.id === selected.id
          return (
            <div
              key={assistant.id}
              onClick={() => onSelect(assistant.id)}
              style={{
                padding: '18px 16px',
                borderRadius: 14,
                border: `2px solid ${isActive ? assistant.color : 'var(--border)'}`,
                background: isActive ? `${assistant.color}0a` : 'var(--bg-primary)',
                cursor: 'pointer',
                transition: 'all 0.15s',
              }}>
              <div style={{ fontSize: 28, marginBottom: 10 }}>{assistant.icon}</div>
              <div
                style={{
                  fontWeight: 700,
                  fontSize: 15,
                  marginBottom: 4,
                  color: isActive ? assistant.color : 'var(--text-primary)',
                }}>
                {getName(assistant)}
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5 }}>{getDesc(assistant)}</div>
            </div>
          )
        })}
      </div>

      <div style={{ padding: 24, borderRadius: 16, border: `1px solid ${selected.color}30`, background: `${selected.color}08` }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
          <span style={{ fontSize: 32 }}>{selected.icon}</span>
          <div>
            <div style={{ fontWeight: 700, fontSize: 18, color: selected.color }}>{getName(selected)}</div>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{getDesc(selected)}</div>
          </div>
        </div>
        <button
          onClick={() => onStartChat(selected.id)}
          style={{
            padding: '12px 28px',
            borderRadius: 12,
            border: 'none',
            background: selected.color,
            color: '#fff',
            fontWeight: 700,
            fontSize: 15,
            cursor: 'pointer',
            transition: 'opacity 0.15s',
          }}>
          {t.assist_start_chat}
        </button>
      </div>
    </div>
  )
}

export default Assistants
