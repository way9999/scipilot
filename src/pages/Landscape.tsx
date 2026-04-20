import { type FC, useState } from 'react'
import { DISCIPLINE_OPTIONS } from '../types/workbench'
import type { LandscapeResult } from '../types/workbench'
import * as api from '../lib/tauri'
import { useT } from '../i18n/context'

const Landscape: FC = () => {
  const [topic, setTopic] = useState('')
  const [discipline, setDiscipline] = useState('generic')
  const [limit, setLimit] = useState(20)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<LandscapeResult | null>(null)
  const [activeTab, setActiveTab] = useState<'table' | 'diagram' | 'stats'>('table')
  const t = useT()

  const disciplineLabels: Record<string, string> = {
    generic: t.discipline_generic, cs: t.discipline_cs, physics: t.discipline_physics,
    bio: t.discipline_bio, chemistry: t.discipline_chemistry, materials: t.discipline_materials,
    energy: t.discipline_energy, economics: t.discipline_economics,
  }

  const handleAnalyze = async () => {
    if (!topic.trim() || loading) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const resp = await api.landscapeAnalyze(topic.trim(), discipline, limit)
      if (!resp.success) throw new Error(resp.error || 'Analysis failed')
      setResult(resp.data as LandscapeResult)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  const tabStyle = (active: boolean): React.CSSProperties => ({
    padding: '8px 16px',
    borderRadius: 8,
    border: 'none',
    background: active ? 'var(--accent)' : 'transparent',
    color: active ? '#fff' : 'var(--text-secondary)',
    cursor: 'pointer',
    fontWeight: active ? 600 : 400,
    fontSize: 14,
  })

  return (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 18, height: '100%', overflow: 'auto' }}>
      <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>{t.landscape_title}</h1>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1.6fr 160px 100px auto',
          gap: 12,
          padding: 18,
          borderRadius: 16,
          background: 'var(--bg-primary)',
          border: '1px solid var(--border)',
        }}
      >
        <input
          type="text"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleAnalyze()}
          placeholder={t.landscape_placeholder}
          style={{ padding: '10px 14px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontSize: 14, outline: 'none' }}
        />
        <select
          value={discipline}
          onChange={(e) => setDiscipline(e.target.value)}
          style={{ padding: '10px 12px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontSize: 14 }}
        >
          {DISCIPLINE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{disciplineLabels[opt.value] || opt.label}</option>
          ))}
        </select>
        <input type="number" value={limit} onChange={(e) => setLimit(Math.max(1, Math.min(50, Number(e.target.value))))} min={1} max={50}
          style={{ padding: '10px 12px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontSize: 14, outline: 'none' }}
        />
        <button onClick={handleAnalyze} disabled={loading || !topic.trim()}
          style={{ padding: '10px 24px', borderRadius: 8, border: 'none', background: 'var(--accent)', color: '#fff', fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer', opacity: loading || !topic.trim() ? 0.5 : 1 }}
        >
          {loading ? t.landscape_analyzing : t.landscape_analyze}
        </button>
      </div>

      {error && (
        <div style={{ padding: '12px 16px', borderRadius: 10, background: '#fef2f2', border: '1px solid #fecaca', color: '#dc2626' }}>
          {error}
        </div>
      )}

      {loading && (
        <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>
          <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>{t.landscape_searching}</div>
          <div style={{ fontSize: 13 }}>{t.landscape_searching_sub}</div>
        </div>
      )}

      {result && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 14 }}>
            {[
              { label: t.stats_papers, value: result.paper_count },
              { label: t.landscape_methods, value: Object.keys(result.statistics.methods).length },
              { label: t.landscape_tools, value: Object.keys(result.statistics.tools).length },
              { label: t.landscape_metrics, value: Object.keys(result.statistics.metrics).length },
              { label: t.landscape_datasets, value: Object.keys(result.statistics.datasets).length },
            ].map((card) => (
              <div key={card.label} style={{ padding: 16, borderRadius: 14, background: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{card.label}</div>
                <div style={{ fontSize: 26, fontWeight: 700, marginTop: 6 }}>{card.value}</div>
              </div>
            ))}
          </div>

          <div style={{ display: 'flex', gap: 4 }}>
            <button style={tabStyle(activeTab === 'table')} onClick={() => setActiveTab('table')}>{t.landscape_tab_table}</button>
            <button style={tabStyle(activeTab === 'diagram')} onClick={() => setActiveTab('diagram')}>{t.landscape_tab_diagram}</button>
            <button style={tabStyle(activeTab === 'stats')} onClick={() => setActiveTab('stats')}>{t.landscape_tab_stats}</button>
          </div>

          {activeTab === 'table' && (
            <div style={{ padding: 18, borderRadius: 16, background: 'var(--bg-primary)', border: '1px solid var(--border)', overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    {['#', t.landscape_col_paper, t.landscape_col_year, t.landscape_col_tools, t.landscape_col_methods, t.landscape_col_contribution].map((h) => (
                      <th key={h} style={{ padding: '10px 12px', textAlign: 'left', fontSize: 12, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.06em', background: 'var(--bg-secondary)', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.papers.map((paper, i) => (
                    <tr key={paper.paper_id || i} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '10px 12px', color: 'var(--text-tertiary)', fontSize: 13 }}>{i + 1}</td>
                      <td style={{ padding: '10px 12px', minWidth: 240 }}>
                        <div style={{ fontWeight: 600, fontSize: 13 }}>{paper.title || t.papers_untitled}</div>
                        <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 3 }}>
                          {paper.authors?.slice(0, 2).join(', ') || '—'}
                          {paper.venue ? ` · ${paper.venue}` : ''}
                        </div>
                      </td>
                      <td style={{ padding: '10px 12px', color: 'var(--text-secondary)', fontSize: 13 }}>{paper.year || '—'}</td>
                      <td style={{ padding: '10px 12px' }}>
                        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                          {paper.tools.length > 0
                            ? paper.tools.slice(0, 3).map((tool) => <Chip key={tool} color="#3b82f6">{tool}</Chip>)
                            : <span style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>—</span>}
                        </div>
                      </td>
                      <td style={{ padding: '10px 12px' }}>
                        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                          {paper.methods.length > 0
                            ? paper.methods.slice(0, 3).map((m) => <Chip key={m} color="#8b5cf6">{m}</Chip>)
                            : <span style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>—</span>}
                        </div>
                      </td>
                      <td style={{ padding: '10px 12px', fontSize: 13, color: 'var(--text-secondary)', maxWidth: 300 }}>
                        {paper.contribution
                          ? (paper.contribution.length > 120 ? paper.contribution.slice(0, 120) + '...' : paper.contribution)
                          : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {activeTab === 'diagram' && (
            <div style={{ padding: 18, borderRadius: 16, background: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
              <h2 style={{ margin: '0 0 12px', fontSize: 16, fontWeight: 600 }}>{t.landscape_mermaid_title}</h2>
              <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 12 }}>{t.landscape_mermaid_hint}</div>
              <pre style={{ padding: 16, borderRadius: 10, background: 'var(--bg-secondary)', border: '1px solid var(--border)', overflow: 'auto', fontSize: 13, lineHeight: 1.6, color: 'var(--text-primary)' }}>
                {result.mermaid_diagram}
              </pre>
              <h3 style={{ margin: '18px 0 10px', fontSize: 14, fontWeight: 600 }}>{t.landscape_clusters}</h3>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 10 }}>
                {Object.entries(result.method_clusters).map(([cluster, methods]) => (
                  <div key={cluster} style={{ padding: '12px 14px', borderRadius: 10, background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
                    <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>{cluster}</div>
                    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                      {methods.map((m) => <Chip key={m} color="#8b5cf6">{m}</Chip>)}
                    </div>
                  </div>
                ))}
              </div>
              <h3 style={{ margin: '18px 0 10px', fontSize: 14, fontWeight: 600 }}>{t.landscape_trends}</h3>
              <p style={{ color: 'var(--text-secondary)', fontSize: 14, lineHeight: 1.7 }}>{result.trend_summary}</p>
            </div>
          )}

          {activeTab === 'stats' && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
              <StatPanel title={t.landscape_methods} items={result.statistics.methods} color="#8b5cf6" noDataText={t.landscape_no_data} />
              <StatPanel title={t.landscape_tools} items={result.statistics.tools} color="#3b82f6" noDataText={t.landscape_no_data} />
              <StatPanel title={t.landscape_metrics} items={result.statistics.metrics} color="#10b981" noDataText={t.landscape_no_data} />
              <StatPanel title={t.landscape_datasets} items={result.statistics.datasets} color="#f59e0b" noDataText={t.landscape_no_data} />
              <StatPanel title={t.landscape_years} items={result.statistics.years} color="#6366f1" noDataText={t.landscape_no_data} />
              <StatPanel title={t.landscape_venues} items={result.statistics.venues} color="#ec4899" noDataText={t.landscape_no_data} />
            </div>
          )}
        </>
      )}
    </div>
  )
}

const Chip: FC<{ children: React.ReactNode; color?: string }> = ({ children, color }) => (
  <span style={{ padding: '3px 8px', borderRadius: 999, fontSize: 11, fontWeight: 500, background: color ? `${color}18` : 'var(--bg-secondary)', color: color || 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
    {children}
  </span>
)

const StatPanel: FC<{ title: string; items: Record<string, number>; color: string; noDataText: string }> = ({ title, items, color, noDataText }) => {
  const entries = Object.entries(items).sort(([, a], [, b]) => b - a).slice(0, 12)
  const max = entries.length > 0 ? entries[0][1] : 1
  return (
    <div style={{ padding: 18, borderRadius: 16, background: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
      <h3 style={{ margin: '0 0 12px', fontSize: 14, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{title}</h3>
      {entries.length > 0 ? (
        <div style={{ display: 'grid', gap: 6 }}>
          {entries.map(([name, count]) => (
            <div key={name} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ fontSize: 13, width: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flexShrink: 0 }}>{name}</span>
              <div style={{ flex: 1, height: 6, borderRadius: 3, background: 'var(--bg-secondary)' }}>
                <div style={{ height: '100%', borderRadius: 3, background: color, width: `${(count / max) * 100}%`, transition: 'width 0.3s' }} />
              </div>
              <span style={{ fontSize: 12, color: 'var(--text-tertiary)', width: 24, textAlign: 'right', flexShrink: 0 }}>{count}</span>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ color: 'var(--text-tertiary)', fontSize: 13 }}>{noDataText}</div>
      )}
    </div>
  )
}

export default Landscape
