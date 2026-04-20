import { type FC, useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useAppDispatch, useAppSelector } from '../store'
import { addMessage, appendStreamChunk, finishStreaming, startStreaming } from '../store/chatSlice'
import { useT } from '../i18n/context'
import * as api from '../lib/tauri'

interface LLMChatProps {
  conversationId: string
  systemPrompt: string
  provider?: string
  model?: string
  enableTools?: boolean
}

function detectToolIntent(text: string): { tool: string; args: Record<string, unknown> } | null {
  if (/(全景|综述|综覽|landscape|overview|survey|分析.*领域|领域.*分析|研究现状)/i.test(text)) {
    const topicMatch =
      text.match(/[“"《](.+?)[”"》]/) ||
      text.match(/关于(.+?)(的|研究|全景|综述|分析)/) ||
      text.match(/分析(.+?)(的|领域|方法|研究)?/)
    const topic = (topicMatch?.[1] || text).replace(/全景|综述|综覽|landscape|overview|survey|分析/gi, '').trim()
    const discipline = detectDiscipline(text)
    return { tool: 'landscape_analyze', args: { topic, discipline, limit: 20 } }
  }

  if (/(搜索|查找|找.*论文|找.*文献|检索|search|find.*paper|look.*paper)/i.test(text)) {
    const query = text
      .replace(/搜索|查找|帮我找|找一下|找论文|找文献|检索|search for|find papers on|look for/gi, '')
      .trim()
    if (query.length < 2) {
      return null
    }
    const discipline = detectDiscipline(text)
    return { tool: 'search_papers', args: { query, discipline, limit: 10, download: false } }
  }

  return null
}

function detectDiscipline(text: string): string {
  const lower = text.toLowerCase()
  if (/(dna|rna|protein|cell|bio|gene|receptor|antibody|drug|生物|医药|药物)/i.test(lower)) return 'bio'
  if (/(material|crystal|alloy|polymer|材料|合金|晶体)/i.test(lower)) return 'materials'
  if (/(chemistry|molecule|reaction|synthesis|化学|分子|合成)/i.test(lower)) return 'chemistry'
  if (/(physics|quantum|particle|物理|量子)/i.test(lower)) return 'physics'
  if (/(energy|solar|battery|能源|电池|光伏)/i.test(lower)) return 'energy'
  if (/(econom|finance|market|经济|金融|市场)/i.test(lower)) return 'economics'
  if (/(neural|deep learning|transformer|llm|gnn|ai|机器学习|深度学习|人工智能)/i.test(lower)) return 'cs'
  return 'generic'
}

async function runTool(tool: string, args: Record<string, unknown>): Promise<string> {
  try {
    let response
    switch (tool) {
      case 'search_papers':
        response = await api.searchPapers(
          args.query as string,
          (args.discipline as string) || 'generic',
          (args.limit as number) || 10,
          (args.download as boolean) || false
        )
        break
      case 'landscape_analyze':
        response = await api.landscapeAnalyze(
          args.topic as string,
          (args.discipline as string) || 'generic',
          (args.limit as number) || 20
        )
        break
      case 'get_papers':
        response = await api.getPapers(args.discipline as string, args.source as string)
        break
      default:
        return `Unknown tool: ${tool}`
    }
    return JSON.stringify(response.data, null, 2)
  } catch (error) {
    return `Tool error: ${String(error)}`
  }
}

const LLMChat: FC<LLMChatProps> = ({
  conversationId,
  systemPrompt,
  provider = 'llm',
  model = 'gpt-4o',
  enableTools = false,
}) => {
  const dispatch = useAppDispatch()
  const t = useT()
  const { streaming, streamContent } = useAppSelector((s) => s.chat)
  const conversation = useAppSelector((s) => s.chat.conversations.find((item) => item.id === conversationId))
  const messages = conversation?.messages ?? []

  const [input, setInput] = useState('')
  const [toolStatus, setToolStatus] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const accumulatedRef = useRef('')

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamContent])

  useEffect(() => {
    inputRef.current?.focus()
  }, [conversationId])

  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  const handleStop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const handleSend = useCallback(async () => {
    const text = input.trim()
    if (!text || streaming) return

    dispatch(addMessage({ conversationId, message: { role: 'user', content: text } }))
    setInput('')
    setToolStatus(null)
    dispatch(startStreaming())
    accumulatedRef.current = ''

    let toolContext = ''
    if (enableTools) {
      const intent = detectToolIntent(text)
      if (intent) {
        setToolStatus(`${t.chat_tool_running} ${intent.tool}...`)
        const result = await runTool(intent.tool, intent.args)
        toolContext =
          `\n\n<tool_result tool="${intent.tool}" args='${JSON.stringify(intent.args)}'>\n` +
          `${result}\n</tool_result>\n\n` +
          '上面是工具返回的真实数据，请优先基于这些数据回答，不要编造论文信息。'
      }
    }

    const allMessages = [
      { role: 'system' as const, content: systemPrompt },
      ...messages,
      { role: 'user' as const, content: toolContext ? `${text}${toolContext}` : text },
    ]

    const controller = new AbortController()
    abortRef.current = controller

    try {
      const sidecarUrl = await api.getSidecarUrl()
      const config = await api.getLlmConfig(provider)
      const response = await fetch(`${sidecarUrl}/api/llm/stream`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          provider,
          model: config.model ?? model,
          max_tokens: 4096,
          messages: allMessages,
          stream: true,
        }),
      })

      if (!response.ok) {
        const text = await response.text()
        throw new Error(`Sidecar proxy error ${response.status}: ${text.slice(0, 300)}`)
      }

      const reader = response.body?.getReader()
      if (!reader) {
        throw new Error('No stream reader available.')
      }

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const blocks = buffer.split('\n\n')
        buffer = blocks.pop() ?? ''

        for (const block of blocks) {
          for (const line of block.split('\n')) {
            if (!line.startsWith('data: ')) continue
            const data = line.slice(6).trim()
            if (data === '[DONE]') {
              dispatch(finishStreaming({ conversationId, content: accumulatedRef.current || '(empty response)' }))
              return
            }

            try {
              const parsed = JSON.parse(data)
              const choice = parsed?.choices?.[0]
              const delta = choice?.delta?.content ?? parsed?.delta?.text
              if (delta) {
                accumulatedRef.current += delta
                dispatch(appendStreamChunk(delta))
              }
              if (choice?.finish_reason || parsed?.type === 'message_stop') {
                dispatch(finishStreaming({ conversationId, content: accumulatedRef.current || '(empty response)' }))
                return
              }
            } catch {
              // ignore malformed SSE chunks
            }
          }
        }
      }

      dispatch(finishStreaming({ conversationId, content: accumulatedRef.current || '(empty response)' }))
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        const stoppedContent = accumulatedRef.current || t.chat_stopped
        dispatch(finishStreaming({ conversationId, content: stoppedContent }))
        return
      }
      const message = error instanceof Error ? error.message : String(error)
      dispatch(finishStreaming({ conversationId, content: `Error: ${message}` }))
    } finally {
      abortRef.current = null
      setToolStatus(null)
    }
  }, [conversationId, dispatch, enableTools, input, messages, model, provider, streaming, systemPrompt, t])

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void handleSend()
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <div style={{ flex: 1, overflow: 'auto', padding: '16px 20px' }}>
        {messages.length === 0 && !streaming && (
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              height: '100%',
              gap: 12,
              color: 'var(--text-tertiary)',
            }}>
            <div style={{ fontSize: 36, opacity: 0.5 }}>✦</div>
            <div style={{ fontSize: 15, fontWeight: 500 }}>{t.chat_start}</div>
            <div style={{ fontSize: 13 }}>{t.chat_start_hint}</div>
          </div>
        )}

        {messages.map((message, index) => (
          <MessageBubble key={index} message={message} />
        ))}

        {toolStatus && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '10px 14px',
              borderRadius: 10,
              background: 'rgba(99,102,241,0.08)',
              border: '1px solid rgba(99,102,241,0.2)',
              marginBottom: 12,
              fontSize: 13,
              color: 'var(--accent)',
            }}>
            <Dot delay={0} />
            <Dot delay={150} />
            <Dot delay={300} />
            <span style={{ marginLeft: 4 }}>{toolStatus}</span>
          </div>
        )}

        {streaming && streamContent && <MessageBubble message={{ role: 'assistant', content: streamContent }} isStreaming />}

        {streaming && !streamContent && !toolStatus && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 0', color: 'var(--text-tertiary)', fontSize: 13 }}>
            <span style={{ display: 'inline-flex', gap: 3 }}>
              <Dot delay={0} />
              <Dot delay={150} />
              <Dot delay={300} />
            </span>
            {t.chat_thinking}
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      <div style={{ padding: '12px 16px', borderTop: '1px solid var(--border)', background: 'var(--bg-primary)' }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end' }}>
          <textarea
            ref={inputRef}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t.chat_placeholder}
            rows={1}
            style={{
              flex: 1,
              padding: '10px 14px',
              borderRadius: 12,
              border: '1px solid var(--border)',
              background: 'var(--bg-secondary)',
              color: 'var(--text-primary)',
              fontSize: 14,
              lineHeight: 1.5,
              outline: 'none',
              resize: 'none',
              minHeight: 42,
              maxHeight: 160,
              fontFamily: 'inherit',
            }}
            onInput={(event) => {
              const element = event.currentTarget
              element.style.height = 'auto'
              element.style.height = `${Math.min(element.scrollHeight, 160)}px`
            }}
          />

          {streaming ? (
            <button
              onClick={handleStop}
              style={{
                padding: '10px 20px',
                borderRadius: 12,
                border: '1px solid #fca5a5',
                background: '#fff1f2',
                color: '#dc2626',
                fontWeight: 600,
                fontSize: 14,
                cursor: 'pointer',
                height: 42,
                whiteSpace: 'nowrap',
              }}>
              {t.chat_stop}
            </button>
          ) : (
            <button
              onClick={() => void handleSend()}
              disabled={!input.trim()}
              style={{
                padding: '10px 20px',
                borderRadius: 12,
                border: 'none',
                background: 'var(--accent)',
                color: '#fff',
                fontWeight: 600,
                fontSize: 14,
                cursor: !input.trim() ? 'not-allowed' : 'pointer',
                opacity: !input.trim() ? 0.5 : 1,
                height: 42,
                whiteSpace: 'nowrap',
                transition: 'opacity 0.15s',
              }}>
              {t.chat_send}
            </button>
          )}
        </div>

        <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 6, textAlign: 'center' }}>
          {enableTools ? '工具已启用 · ' : ''}
          {provider}/{model}
        </div>
      </div>
    </div>
  )
}

const MessageBubble: FC<{ message: { role: string; content: string }; isStreaming?: boolean }> = ({ message, isStreaming }) => {
  const isUser = message.role === 'user'
  const displayContent = isUser ? message.content.replace(/<tool_result[\s\S]*?<\/tool_result>/g, '').trim() : message.content

  return (
    <div style={{ display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start', marginBottom: 16 }}>
      {!isUser && (
        <div
          style={{
            width: 32,
            height: 32,
            borderRadius: 10,
            background: 'var(--accent-bg)',
            color: 'var(--accent)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 14,
            fontWeight: 700,
            flexShrink: 0,
            marginRight: 10,
            marginTop: 2,
          }}>
          S
        </div>
      )}

      <div
        style={{
          maxWidth: '80%',
          padding: isUser ? '10px 14px' : '12px 16px',
          borderRadius: isUser ? '16px 16px 4px 16px' : '16px 16px 16px 4px',
          background: isUser ? 'var(--accent)' : 'var(--bg-secondary)',
          color: isUser ? '#fff' : 'var(--text-primary)',
          fontSize: 14,
          lineHeight: 1.7,
        }}>
        {isUser ? (
          <div style={{ whiteSpace: 'pre-wrap' }}>{displayContent}</div>
        ) : (
          <div className="markdown-body">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
            {isStreaming && (
              <span
                style={{
                  display: 'inline-block',
                  width: 6,
                  height: 16,
                  background: 'var(--accent)',
                  borderRadius: 1,
                  marginLeft: 2,
                  animation: 'blink 1s infinite',
                  verticalAlign: 'text-bottom',
                }}
              />
            )}
          </div>
        )}
      </div>
    </div>
  )
}

const Dot: FC<{ delay: number }> = ({ delay }) => (
  <span
    style={{
      width: 5,
      height: 5,
      borderRadius: '50%',
      background: 'currentColor',
      display: 'inline-block',
      animation: `dotPulse 1.2s ${delay}ms infinite`,
    }}
  />
)

export default LLMChat
