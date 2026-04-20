import { type FC, useEffect } from 'react'
import { useAppDispatch, useAppSelector } from '../store'
import { fetchDashboard, refreshWorkbench, fetchRecommendations } from '../store/researchSlice'
import { batchDownloadPapers, batchVerifyPapers, fetchPapers } from '../store/papersSlice'
import StageProgress from '../components/StageProgress'
import type { Recommendation } from '../types/workbench'
import { useT } from '../i18n/context'

type Page = 'dashboard' | 'assistants' | 'search' | 'papers' | 'pipeline' | 'landscape' | 'writing' | 'settings'

interface PipelineProps {
  onNavigate?: (page: Page) => void
}

const REC_COLORS: Record<string, string> = {
  action: '#6366f1',
  warning: '#f59e0b',
  info: '#3b82f6',
  suggestion: '#10b981',
}

const Pipeline: FC<PipelineProps> = ({ onNavigate }) => {
  const dispatch = useAppDispatch()
  const { projectState, route, loading, recommendations } = useAppSelector((s) => s.research)
  const { items: papers, batchLoading } = useAppSelector((s) => s.papers)
  const t = useT()

  useEffect(() => {
    dispatch(fetchDashboard())
    dispatch(fetchPapers())
    dispatch(fetchRecommendations())
  }, [dispatch])

  const handleRefresh = () => {
    dispatch(refreshWorkbench()).then(() => {
      dispatch(fetchDashboard())
      dispatch(fetchPapers())
      dispatch(fetchRecommendations())
    })
  }

  const handleRecAction = (rec: Recommendation) => {
    switch (rec.action) {
      case 'search':
        onNavigate?.('search')
        break
      case 'batch-verify': {
        const ids = papers.filter(p => !p.verified && p.record_id).map(p => p.record_id!)
        if (ids.length) dispatch(batchVerifyPapers(ids)).then(() => dispatch(fetchPapers()))
        break
      }
      case 'batch-download': {
        const ids = papers.filter(p => !p.downloaded && !p.local_path && p.record_id).map(p => p.record_id!)
        if (ids.length) dispatch(batchDownloadPapers(ids)).then(() => dispatch(fetchPapers()))
        break
      }
      case 'advance':
      case 'freeze':
        onNavigate?.('writing')
        break
      default:
        break
    }
  }

  const currentStage = projectState?.current_stage || 'focus'
  const stageLabel = {
    focus: t.stage_focus,
    literature: t.stage_literature,
    structure: t.stage_structure,
    writing: t.stage_writing,
    complete: t.stage_complete,
  }[currentStage] || t.stage_focus

  const routeLabel = {
    focus: t.route_focus,
    literature: t.route_literature,
    structure: t.route_structure,
    writing: t.route_writing,
    complete: t.route_complete,
  }[currentStage] || t.route_focus

  const btnStyle = (color: string): React.CSSProperties => ({
    padding: '6px 14px', borderRadius: 8, border: `1px solid ${color}40`,
    background: `${color}14`, color, cursor: 'pointer', fontSize: 12,
    fontWeight: 600, whiteSpace: 'nowrap', transition: 'opacity 0.15s',
  })

  return (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>{t.pipeline_title}</h1>
        <button
          onClick={handleRefresh}
          disabled={loading}
          style={{ padding: '8px 16px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--bg-secondary)', color: 'var(--text-primary)', cursor: 'pointer', fontWeight: 500 }}
        >
          {loading ? t.dash_refreshing : t.dash_refresh}
        </button>
      </div>

      {/* Current stage hero */}
      <div style={{ padding: '22px 24px', borderRadius: 20, background: 'linear-gradient(155deg, var(--accent-bg), var(--bg-primary))', border: '1px solid var(--accent-border)' }}>
        <div style={{ fontSize: 12, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          {t.pipeline_current_stage}
        </div>
        <div style={{ fontSize: 24, fontWeight: 700, marginTop: 8 }}>{stageLabel}</div>
        <div style={{ color: 'var(--text-secondary)', marginTop: 8, lineHeight: 1.6 }}>{routeLabel}</div>
        {route?.rationale && route.rationale.length > 0 && (
          <div style={{ color: 'var(--text-tertiary)', marginTop: 12, lineHeight: 1.6 }}>
            {route.rationale.join(' ')}
          </div>
        )}
      </div>

      {/* Stage progress */}
      <div style={{ padding: 18, borderRadius: 16, background: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
        <h2 style={{ margin: '0 0 14px', fontSize: 14, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          {t.pipeline_all_stages}
        </h2>
        <StageProgress stageStatus={projectState?.stage_status} />
      </div>

      {/* Recommendations with action buttons */}
      {recommendations.length > 0 && (
        <div style={{ padding: 18, borderRadius: 16, background: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
          <h2 style={{ margin: '0 0 14px', fontSize: 14, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            {t.dash_recommendations}
          </h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {recommendations.map((rec, i) => {
              const color = REC_COLORS[rec.type] || '#6366f1'
              const actionLabel: Record<string, string> = {
                search: t.nav_search,
                'batch-verify': t.papers_batch_verify,
                'batch-download': t.papers_batch_download,
                advance: t.nav_writing,
                freeze: t.nav_writing,
              }
              return (
                <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 14, padding: '12px 14px', borderRadius: 12, background: `${color}08`, border: `1px solid ${color}20` }}>
                  <div style={{ width: 8, height: 8, borderRadius: 999, background: color, marginTop: 6, flexShrink: 0 }} />
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 600, fontSize: 14, color: 'var(--text-primary)' }}>{rec.title}</div>
                    <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 3, lineHeight: 1.5 }}>{rec.description}</div>
                    {rec.queries && rec.queries.length > 0 && (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
                        {rec.queries.map((q, qi) => (
                          <button key={qi} onClick={() => onNavigate?.('search')} style={btnStyle(color)}>
                            {q}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                  {rec.action && actionLabel[rec.action] && (
                    <button
                      onClick={() => handleRecAction(rec)}
                      disabled={batchLoading}
                      style={btnStyle(color)}
                    >
                      {actionLabel[rec.action]}
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Outline status */}
      <div style={{ padding: 18, borderRadius: 16, background: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
        <h2 style={{ margin: '0 0 14px', fontSize: 14, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          {t.pipeline_outline}
        </h2>
        <div style={{ color: 'var(--text-secondary)' }}>
          {projectState?.outline_frozen ? t.pipeline_outline_frozen : t.pipeline_outline_not_frozen}
        </div>
      </div>
    </div>
  )
}

export default Pipeline

