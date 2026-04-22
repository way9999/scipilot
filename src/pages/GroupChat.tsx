import { type FC, useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'

/** Check if running inside Tauri webview */
const isTauri = () => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
import { useAppDispatch, useAppSelector } from '../store'
import {
  createGroupConversation,
  addGroupMessage,
  startModelStream,
  appendModelStream,
  finishModelStream,
  clearAllStreams,
  setMode,
  toggleParticipant,
  updateParticipant,
  setConsensus,
} from '../store/groupChatSlice'
import type { ModelParticipant } from '../types/groupChat'

/** Read knowledge-base papers to inject as context */
async function loadKnowledgeContext(): Promise<string> {
  try {
    const bib = await invoke<string>('read_project_file', { relPath: 'knowledge-base/sources.bib' })
    const entries = bib ? bib.split('@').filter((e: string) => e.trim()) : []
    const bibSummaries = entries.slice(0, 5).map((e: string) => {
      const title = e.match(/title\s*=\s*\{([^}]*)\}/)?.[1] || ''
      const author = e.match(/author\s*=\s*\{([^}]*)\}/)?.[1] || ''
      const year = e.match(/year\s*=\s*\{([^}]*)\}/)?.[1] || ''
      return title ? `- ${title} (${author.split(',')[0]}, ${year})` : ''
    }).filter(Boolean)
    let paperSummaries: string[] = []
    try {
      const papersIndex = await invoke<string>('read_project_file', { relPath: 'knowledge-base/papers/index.json' })
      const papers = papersIndex ? JSON.parse(papersIndex) : []
      paperSummaries = papers.slice(0, 5).map((p: { title?: string; abstract?: string }) => {
        const abstract = p.abstract?.slice(0, 200) || ''
        return p.title ? `- ${p.title}${abstract ? ': ' + abstract + '...' : ''}` : ''
      }).filter(Boolean)
    } catch { /* ignore */ }
    const all = [...bibSummaries, ...paperSummaries].slice(0, 10)
    return all.length > 0 ? `\n\n[Knowledge Base References]\n${all.join('\n')}` : ''
  } catch {
    return ''
  }
}

/** Parse @mentions from input */
function parseMentions(text: string, participants: ModelParticipant[]): {
  cleanText: string
  mentionedIds: string[]
} {
  const mentions: string[] = []
  let cleanText = text
  for (const m of text.matchAll(/@(\w+)/g)) {
    const name = m[1].toLowerCase()
    const p = participants.find(p => p.name.toLowerCase() === name || p.id.toLowerCase() === name)
    if (p) {
      mentions.push(p.id)
      cleanText = cleanText.replace(m[0], '').trim()
    }
  }
  return { cleanText, mentionedIds: mentions }
}

const GroupChat: FC = () => {
  const dispatch = useAppDispatch()
  const { conversations, activeConversationId, participants, mode, streaming, consensus } =
    useAppSelector((s) => s.groupChat)

  const [input, setInput] = useState('')
  const [showSettings, setShowSettings] = useState(false)
  const [editingModel, setEditingModel] = useState<string | null>(null)
  const [knowledgeEnabled, setKnowledgeEnabled] = useState(true)
  const [knowledgeContext, setKnowledgeContext] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // Use refs for mutable state that event listeners need to access without stale closures
  const enabledParticipantsRef = useRef<ModelParticipant[]>([])
  const modeRef = useRef(mode)
  const activeConvIdRef = useRef(activeConversationId)
  const streamingRef = useRef(streaming)
  const messagesRef = useRef(conversations.find(c => c.id === activeConversationId)?.messages ?? [])
  const knowledgeCtxRef = useRef(knowledgeContext)
  const knowledgeEnabledRef = useRef(knowledgeEnabled)

  // Track model completion for chaining and consensus
  const expectedModelsRef = useRef<Set<string>>(new Set())
  const completedModelsRef = useRef<Map<string, string>>(new Map()) // modelId → content
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const activeConversation = conversations.find((c) => c.id === activeConversationId)
  const messages = activeConversation?.messages ?? []
  const enabledParticipants = participants.filter((p) => p.enabled)
  const isStreaming = Object.keys(streaming).length > 0

  // Keep refs in sync with state
  useEffect(() => { enabledParticipantsRef.current = enabledParticipants }, [enabledParticipants])
  useEffect(() => { modeRef.current = mode }, [mode])
  useEffect(() => { activeConvIdRef.current = activeConversationId }, [activeConversationId])
  useEffect(() => { streamingRef.current = streaming }, [streaming])
  useEffect(() => { messagesRef.current = messages }, [messages])
  useEffect(() => { knowledgeCtxRef.current = knowledgeContext }, [knowledgeContext])
  useEffect(() => { knowledgeEnabledRef.current = knowledgeEnabled }, [knowledgeEnabled])

  // Load model name from settings on mount
  useEffect(() => {
    if (!isTauri()) return
    ;(async () => {
      try {
        const config = await invoke<{ model: string }>('get_llm_config', { provider: 'llm' })
        if (config.model) {
          // Set model-a to the user's configured default model
          dispatch(updateParticipant({ id: 'model-a', changes: { model: config.model, name: config.model } }))
          dispatch(updateParticipant({ id: 'model-b', changes: { model: config.model } }))
          dispatch(updateParticipant({ id: 'model-c', changes: { model: config.model } }))
        }
      } catch { /* ignore */ }
    })()
  }, [dispatch])

  // Load knowledge context
  useEffect(() => {
    if (knowledgeEnabled) loadKnowledgeContext().then(setKnowledgeContext)
  }, [knowledgeEnabled])

  // Scroll to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, streaming])

  /** Run a single model — used for debate/relay chaining */
  const runSingleModel = useCallback((model: ModelParticipant, prompt: string, roleOverride?: string) => {
    const convId = activeConvIdRef.current
    if (!convId) return

    dispatch(startModelStream({ modelId: model.id }))
    expectedModelsRef.current.add(model.id)

    const currentMessages = messagesRef.current
    const history = currentMessages
      .filter((m) => m.role === 'user' || m.role === 'assistant')
      .map((m) => ({ role: m.role, content: m.content }))
    history.push({ role: 'user' as const, content: prompt })

    invoke('group_chat_stream', {
      requests: [{
        model_id: model.id,
        provider: model.provider,
        model: model.model,
        system_prompt: getSystemPromptForRole(roleOverride || model.role) + (knowledgeEnabledRef.current ? knowledgeCtxRef.current : ''),
      }],
      history,
    }).catch(err => console.error('Chain model error:', err))
  }, [dispatch])

  /** Handle completion of all expected models in current round */
  const onRoundComplete = useCallback(() => {
    const completed = completedModelsRef.current
    const m = modeRef.current
    const models = enabledParticipantsRef.current
    const entries = Array.from(completed.entries())
    const lastContent = entries[entries.length - 1]?.[1] || ''

    if (m === 'debate' && models.length > 1) {
      const round = completed.size
      if (round < models.length && round < 3) {
        const nextModel = models[round % models.length]
        completedModelsRef.current = new Map(entries)
        runSingleModel(nextModel,
          `请对以下观点进行批判性分析，指出潜在问题或补充不同视角：\n\n${lastContent}`,
          'critic'
        )
        return
      }
    }

    if (m === 'relay' && models.length > 1) {
      const step = completed.size
      if (step < models.length) {
        const stepNames = ['设计方案', '优化改进', '验证评估', '进一步处理']
        const nextModel = models[step]
        completedModelsRef.current = new Map(entries)
        runSingleModel(nextModel,
          `请对以下内容进行${stepNames[Math.min(step, 3)]}：\n\n${lastContent}`,
          step === 1 ? 'creative' : 'analyst'
        )
        return
      }
    }

    // All done — consensus analysis
    if (entries.length >= 2) {
      triggerConsensus(entries.map(([, content]) => content))
    }
    completedModelsRef.current = new Map()
    expectedModelsRef.current = new Set()
  }, [runSingleModel])

  /** Deep consensus analysis using LLM */
  const triggerConsensus = useCallback(async (responses: string[]) => {
    if (responses.length < 2) return
    const judge = enabledParticipantsRef.current.find(p => p.role === 'judge') || enabledParticipantsRef.current[0]
    if (!judge) return

    const analysisPrompt = [
      '分析以下多个AI回答，找出共识点和分歧点。',
      '返回JSON：{ "type": "consensus"|"divergence"|"partial", "summary": "一句话总结", "points": [{ "text": "具体观点", "agreement": 0.8 }] }',
      '',
      ...responses.map((r, i) => `--- 模型 ${i + 1} ---\n${r.slice(0, 2000)}`),
    ].join('\n')

    // Collect streamed consensus text
    let consensusText = ''
    if (!isTauri()) {
      // Fallback in non-Tauri environment
      const words = responses.map(r => new Set(r.toLowerCase().split(/\s+/)))
      const common = words.reduce((a, b) => new Set([...a].filter(x => b.has(x))))
      const ratio = common.size / Math.max(...words.map(w => w.size), 1)
      dispatch(setConsensus({
        type: ratio > 0.4 ? 'consensus' : ratio > 0.2 ? 'partial' : 'divergence',
        summary: `${responses.length} 个模型${ratio > 0.4 ? '达成共识' : ratio > 0.2 ? '部分一致' : '存在分歧'}`,
        points: [],
      }))
      dispatch(clearAllStreams())
      return
    }
    const unlisten = await listen<{ model_id: string; delta: string; done: boolean }>(
      'group-chat-chunk',
      (event) => {
        const { model_id, delta, done } = event.payload
        if (model_id === '__consensus__') {
          if (delta) consensusText += delta
          if (done) {
            // Try to parse the LLM response as JSON
            try {
              // Extract JSON from markdown code blocks if present
              const jsonMatch = consensusText.match(/```json\s*([\s\S]*?)```/) ||
                consensusText.match(/\{[\s\S]*\}/)
              const jsonStr = jsonMatch ? jsonMatch[1] || jsonMatch[0] : consensusText
              const parsed = JSON.parse(jsonStr)
              dispatch(setConsensus({
                type: parsed.type || 'partial',
                summary: parsed.summary || '',
                points: parsed.points || [],
              }))
            } catch {
              // Fallback: local heuristic
              const words = responses.map(r => new Set(r.toLowerCase().split(/\s+/)))
              const common = words.reduce((a, b) => new Set([...a].filter(x => b.has(x))))
              const ratio = common.size / Math.max(...words.map(w => w.size), 1)
              dispatch(setConsensus({
                type: ratio > 0.4 ? 'consensus' : ratio > 0.2 ? 'partial' : 'divergence',
                summary: `${responses.length} 个模型${ratio > 0.4 ? '达成共识' : ratio > 0.2 ? '部分一致' : '存在分歧'}`,
                points: [],
              }))
            }
            // Clean up
            dispatch(clearAllStreams())
          }
        }
      }
    )

    // Start consensus analysis stream
    dispatch(startModelStream({ modelId: '__consensus__' }))
    try {
      await invoke('group_chat_stream', {
        requests: [{
          model_id: '__consensus__',
          provider: judge.provider,
          model: judge.model,
          system_prompt: '你是共识分析专家。分析多个AI回答，找出共识和分歧。必须返回JSON格式，不要包含其他文字。',
        }],
        history: [{ role: 'user', content: analysisPrompt }],
      })
    } catch {
      // Local fallback
      const words = responses.map(r => new Set(r.toLowerCase().split(/\s+/)))
      const common = words.reduce((a, b) => new Set([...a].filter(x => b.has(x))))
      const ratio = common.size / Math.max(...words.map(w => w.size), 1)
      dispatch(setConsensus({
        type: ratio > 0.4 ? 'consensus' : ratio > 0.2 ? 'partial' : 'divergence',
        summary: `${responses.length} 个模型${ratio > 0.4 ? '达成共识' : ratio > 0.2 ? '部分一致' : '存在分歧'}`,
        points: [],
      }))
      dispatch(clearAllStreams())
    }
    // Clean up listener after stream completes (give it a moment)
    setTimeout(() => unlisten(), 5000)
  }, [dispatch])

  // Listen for group-chat-chunk events
  useEffect(() => {
    let unlisten: UnlistenFn | undefined
    const setup = async () => {
      if (!isTauri()) return
      unlisten = await listen<{ model_id: string; delta: string; done: boolean }>(
        'group-chat-chunk',
        (event) => {
          const { model_id, delta, done } = event.payload
          const convId = activeConvIdRef.current

          if (model_id === '__consensus__') return // Handled separately in triggerConsensus

          if (done) {
            const content = streamingRef.current[model_id] || ''
            if (convId && content) {
              dispatch(finishModelStream({ conversationId: convId, modelId: model_id, content }))
              completedModelsRef.current.set(model_id, content)

              // Debounce round completion check
              if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current)
              debounceTimerRef.current = setTimeout(() => {
                const expected = expectedModelsRef.current
                const completed = completedModelsRef.current
                // Check if all expected models for this round have finished
                const allDone = [...expected].every(id => completed.has(id))
                if (allDone && completed.size > 0) {
                  onRoundComplete()
                }
              }, 200) // Small debounce to handle concurrent completions
            }
          } else if (delta) {
            dispatch(appendModelStream({ modelId: model_id, chunk: delta }))
          }
        }
      )
    }
    setup()
    return () => { unlisten?.(); if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current) }
  }, [dispatch, onRoundComplete])

  const handleSend = useCallback(async () => {
    if (!input.trim() || enabledParticipants.length === 0) return

    let convId = activeConversationId
    if (!convId) {
      // Generate conversation ID inline so we can use it immediately
      convId = `gc_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
      dispatch(createGroupConversation())
    }

    const { cleanText, mentionedIds } = parseMentions(input, participants)
    const targets = mode === 'free' && mentionedIds.length > 0
      ? enabledParticipants.filter(p => mentionedIds.includes(p.id))
      : enabledParticipants
    if (targets.length === 0 || targets.some(t => !t.model.trim())) return

    dispatch(addGroupMessage({ conversationId: convId, message: {
      id: `msg_${Date.now()}_user`, role: 'user', content: input.trim(), timestamp: new Date().toISOString(),
    }}))

    const history = messages
      .filter((m) => m.role === 'user' || m.role === 'assistant')
      .map((m) => ({ role: m.role, content: m.content }))
    history.push({ role: 'user' as const, content: cleanText })

    // Reset tracking
    completedModelsRef.current = new Map()
    expectedModelsRef.current = new Set(targets.map(p => p.id))
    dispatch(setConsensus(null))
    setInput('')

    const kbCtx = knowledgeEnabled ? knowledgeContext : ''

    try {
      if (mode === 'parallel' || mode === 'free') {
        const reqs = targets.map(p => ({
          model_id: p.id, provider: p.provider, model: p.model,
          system_prompt: getSystemPromptForRole(p.role) + kbCtx,
        }))
        console.log('[group_chat] parallel requests:', reqs.length, reqs.map(r => r.model_id))
        for (const p of targets) dispatch(startModelStream({ modelId: p.id }))
        await invoke('group_chat_stream', { requests: reqs, history })
      } else if (mode === 'debate') {
        const first = targets[0]
        dispatch(startModelStream({ modelId: first.id }))
        // Only expect first model initially; onRoundComplete will add others
        expectedModelsRef.current = new Set([first.id])
        await invoke('group_chat_stream', {
          requests: [{
            model_id: first.id, provider: first.provider, model: first.model,
            system_prompt: getSystemPromptForRole('analyst') + kbCtx,
          }],
          history,
        })
      } else if (mode === 'relay') {
        const first = targets[0]
        dispatch(startModelStream({ modelId: first.id }))
        expectedModelsRef.current = new Set([first.id])
        await invoke('group_chat_stream', {
          requests: [{
            model_id: first.id, provider: first.provider, model: first.model,
            system_prompt: getSystemPromptForRole('creative') + '\n你的任务是提出初步方案或设计。' + kbCtx,
          }],
          history,
        })
      }
    } catch (error) {
      console.error('Group chat error:', error)
    } finally {
      // Don't clear streams here — debate/relay chaining may still be in progress
      // Streams are cleared by individual finishModelStream or onRoundComplete
    }
  }, [input, mode, enabledParticipants, participants, activeConversationId, messages, dispatch, conversations, knowledgeEnabled, knowledgeContext])

  const getSystemPromptForRole = (role?: string): string => {
    switch (role) {
      case 'analyst': return '你是分析专家。专注于逻辑推理、识别假设、评估证据。回答精确、结构化。用用户提问的语言回答。'
      case 'creative': return '你是创意思考者。探索新想法、替代方案、创新解决方案。跳出框架思考但保持实用性。用用户提问的语言回答。'
      case 'researcher': return '你是研究专家。专注于查找相关文献、引用来源、确保论点有证据支持。用用户提问的语言回答。'
      case 'critic': return '你是批判性评估者。识别潜在缺陷、边缘情况、局限性。建设性地挑战假设。用用户提问的语言回答。'
      case 'judge': return '你是中立主持人。综合不同观点，识别共识和分歧，提出平衡方案。用用户提问的语言回答。'
      default: return '你是参与群组讨论的AI助手。用用户提问的语言回答。'
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  const modeLabels = { parallel: '并行模式', debate: '辩论模式', relay: '接力模式', free: '自由模式' }
  const modeDescriptions = {
    parallel: '所有模型同时回答，对比不同视角',
    debate: '模型轮流发言，互相质疑和补充（最多3轮）',
    relay: '分工协作：A设计→B优化→C验证',
    free: '自由群聊，@Claude @GPT 指定模型发言',
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: 'var(--bg-base)' }}>
      {/* Header */}
      <div style={{ padding: '12px 20px', borderBottom: '1px solid var(--border)', background: 'var(--bg-primary)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 18, fontWeight: 600 }}>群聊实验室</span>
          <span style={{ fontSize: 12, padding: '2px 8px', borderRadius: 4, background: 'var(--accent-bg)', color: 'var(--accent)' }}>{modeLabels[mode]}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button onClick={() => setKnowledgeEnabled(!knowledgeEnabled)} style={{ padding: '4px 10px', borderRadius: 6, border: '1px solid var(--border)', background: knowledgeEnabled ? 'var(--accent-bg)' : 'var(--bg-secondary)', color: knowledgeEnabled ? 'var(--accent)' : 'var(--text-secondary)', cursor: 'pointer', fontSize: 12 }}>
            {knowledgeEnabled ? '📚 知识库' : '📚 未注入'}
          </button>
          <select value={mode} onChange={(e) => dispatch(setMode(e.target.value as typeof mode))} style={{ padding: '4px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontSize: 12 }}>
            <option value="parallel">并行</option><option value="debate">辩论</option><option value="relay">接力</option><option value="free">自由</option>
          </select>
          <button onClick={() => setShowSettings(!showSettings)} style={{ padding: '4px 12px', borderRadius: 6, border: '1px solid var(--border)', background: showSettings ? 'var(--accent-bg)' : 'var(--bg-secondary)', color: showSettings ? 'var(--accent)' : 'var(--text-secondary)', cursor: 'pointer', fontSize: 12 }}>模型设置</button>
        </div>
      </div>

      {/* Settings panel */}
      {showSettings && (
        <div style={{ padding: '12px 20px', borderBottom: '1px solid var(--border)', background: 'var(--bg-secondary)', flexShrink: 0 }}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            {participants.map((p) => (
              <div key={p.id} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', borderRadius: 8, background: p.enabled ? `${p.color}20` : 'var(--bg-tertiary)', border: `1px solid ${p.enabled ? p.color : 'var(--border)'}`, cursor: 'pointer', fontSize: 13 }}>
                  <input type="checkbox" checked={p.enabled} onChange={() => dispatch(toggleParticipant(p.id))} style={{ display: 'none' }} />
                  <span style={{ fontSize: 16 }}>{p.avatar}</span>
                  <span style={{ color: p.enabled ? p.color : 'var(--text-tertiary)' }}>{p.name}</span>
                  {editingModel !== p.id ? (
                    <span style={{ fontSize: 11, color: 'var(--text-tertiary)', cursor: 'pointer', textDecoration: 'underline dotted' }} onClick={(e) => { e.preventDefault(); e.stopPropagation(); setEditingModel(p.id) }}>{p.model}</span>
                  ) : (
                    <input
                      value={p.model}
                      onClick={(e) => e.stopPropagation()}
                      onChange={(e) => dispatch(updateParticipant({ id: p.id, changes: { model: e.target.value } }))}
                      onBlur={() => setEditingModel(null)}
                      onKeyDown={(e) => { if (e.key === 'Enter') setEditingModel(null) }}
                      style={{ fontSize: 11, padding: '2px 6px', borderRadius: 4, border: '1px solid var(--border)', background: 'var(--bg-base)', color: 'var(--text-primary)', width: 140 }}
                      autoFocus
                    />
                  )}
                </label>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text-tertiary)' }}>{modeDescriptions[mode]}</div>
        </div>
      )}

      {/* Messages */}
      <div style={{ flex: 1, overflow: 'auto', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 16 }}>
        {messages.length === 0 && !isStreaming && (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)', gap: 12 }}>
            <div style={{ fontSize: 48 }}>{enabledParticipants.map((p) => p.avatar).join(' ')}</div>
            <div style={{ fontSize: 14 }}>向 AI 模型提问，获得不同视角的回答</div>
            <div style={{ fontSize: 12, color: 'var(--text-quaternary)' }}>
              已启用 {enabledParticipants.length} 个模型 — 点击「模型设置」配置模型名称
              {knowledgeEnabled && knowledgeContext && ' · 📚 知识库已注入'}
            </div>
          </div>
        )}
        {messages.map((msg) => {
          const p = participants.find((x) => x.id === msg.model)
          return (
            <div key={msg.id} style={{ display: 'flex', gap: 12, flexDirection: msg.role === 'user' ? 'row-reverse' : 'row' }}>
              <div style={{ width: 36, height: 36, borderRadius: 8, background: msg.role === 'user' ? 'var(--accent)' : p?.color || 'var(--bg-tertiary)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18, flexShrink: 0 }}>
                {msg.role === 'user' ? '👤' : p?.avatar || '🤖'}
              </div>
              <div style={{ flex: 1, maxWidth: '80%', padding: '12px 16px', borderRadius: 12, background: msg.role === 'user' ? 'var(--accent-bg)' : 'var(--bg-primary)', border: `1px solid ${msg.role === 'user' ? 'var(--accent)' : 'var(--border)'}` }}>
                {msg.role === 'assistant' && p && <div style={{ fontSize: 11, fontWeight: 600, color: p.color, marginBottom: 6 }}>{p.name}</div>}
                <div style={{ fontSize: 14, lineHeight: 1.6 }}><ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown></div>
              </div>
            </div>
          )
        })}
        {Object.entries(streaming).map(([modelId, content]) => {
          if (!content || modelId === '__consensus__') return null
          const p = participants.find((x) => x.id === modelId)
          return (
            <div key={modelId} style={{ display: 'flex', gap: 12 }}>
              <div style={{ width: 36, height: 36, borderRadius: 8, background: p?.color || 'var(--bg-tertiary)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18, flexShrink: 0 }}>{p?.avatar || '🤖'}</div>
              <div style={{ flex: 1, maxWidth: '80%', padding: '12px 16px', borderRadius: 12, background: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: p?.color || 'var(--text-secondary)', marginBottom: 6 }}>{p?.name || modelId}<span style={{ marginLeft: 6, opacity: 0.5 }}>生成中...</span></div>
                <div style={{ fontSize: 14, lineHeight: 1.6 }}><ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown></div>
              </div>
            </div>
          )
        })}
        <div ref={bottomRef} />
      </div>

      {/* Consensus */}
      {consensus && (
        <div style={{ padding: '10px 20px', borderTop: '1px solid var(--border)', background: consensus.type === 'consensus' ? '#f0fdf4' : consensus.type === 'divergence' ? '#fef2f2' : 'var(--bg-secondary)', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 14 }}>{consensus.type === 'consensus' ? '✓' : consensus.type === 'divergence' ? '⚠' : '○'}</span>
            <span style={{ fontSize: 12, fontWeight: 600 }}>{consensus.type === 'consensus' ? '共识' : consensus.type === 'divergence' ? '分歧' : '部分一致'}</span>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{consensus.summary}</span>
          </div>
          {consensus.points.length > 0 && (
            <div style={{ marginTop: 6 }}>
              {consensus.points.map((pt, i) => (
                <div key={i} style={{ fontSize: 11, color: 'var(--text-secondary)', padding: '2px 0' }}>
                  {pt.agreement > 0.6 ? '✓' : '⚠'} {pt.text} ({Math.round(pt.agreement * 100)}%)
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Input */}
      <div style={{ padding: '16px 20px', borderTop: '1px solid var(--border)', background: 'var(--bg-primary)', flexShrink: 0 }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end' }}>
          <textarea ref={inputRef} value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={handleKeyDown}
            placeholder={mode === 'free' ? `输入问题，或 @${enabledParticipants.map(p => p.name).join(' @')} 指定模型...` : enabledParticipants.length > 0 ? `向 ${enabledParticipants.length} 个模型提问...` : '请先在模型设置中启用模型'}
            style={{ flex: 1, padding: '12px 16px', borderRadius: 12, border: '1px solid var(--border)', background: 'var(--bg-base)', color: 'var(--text-primary)', fontSize: 14, resize: 'none', minHeight: 48, maxHeight: 200, lineHeight: 1.5 }} rows={1} />
          <button onClick={handleSend} disabled={!input.trim() || enabledParticipants.length === 0 || isStreaming}
            style={{ padding: '12px 24px', borderRadius: 12, border: 'none', background: input.trim() && enabledParticipants.length > 0 && !isStreaming ? 'var(--accent)' : 'var(--bg-tertiary)', color: input.trim() && enabledParticipants.length > 0 && !isStreaming ? '#fff' : 'var(--text-tertiary)', cursor: input.trim() && enabledParticipants.length > 0 && !isStreaming ? 'pointer' : 'not-allowed', fontSize: 14, fontWeight: 600 }}>
            发送
          </button>
        </div>
      </div>
    </div>
  )
}

export default GroupChat
