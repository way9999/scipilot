import { type FC, useEffect } from 'react'
import { useAppDispatch, useAppSelector } from '../store'
import { fetchDashboard, checkSidecarHealth, refreshWorkbench, fetchRecommendations } from '../store/researchSlice'
import { fetchPapers } from '../store/papersSlice'
import StageProgress from '../components/StageProgress'
import StatsCards from '../components/StatsCards'
import { useT } from '../i18n/context'

const Dashboard: FC = () => {
  const dispatch = useAppDispatch()
  const t = useT()
  const { projectState, route, summary, loading, sidecarOnline, error, recommendations } =
    useAppSelector((s) => s.research)
  const { items: papers } = useAppSelector((s) => s.papers)

  useEffect(() => {
    dispatch(checkSidecarHealth())
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

  return (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 18 }}>
      {/* Header */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr', gap: 16 }}>
        <div
          style={{
            padding: '22px 24px',
            borderRadius: 20,
            background: 'linear-gradient(155deg, var(--accent-bg), var(--bg-primary))',
            border: '1px solid var(--accent-border)',
          }}
        >
          <div
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              color: 'var(--accent)',
              fontSize: 12,
              fontWeight: 700,
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
            }}
          >
            <span
              style={{
                width: 10,
                height: 10,
                borderRadius: 999,
                background: sidecarOnline ? '#10b981' : '#ef4444',
              }}
            />
            {sidecarOnline ? t.dash_online : t.dash_offline}
          </div>
          <h1 style={{ margin: '14px 0 10px', fontSize: 28, lineHeight: 1.1 }}>
            {t.dash_title}
          </h1>
          <p style={{ color: 'var(--text-secondary)', lineHeight: 1.7, margin: '0 0 16px' }}>
            {projectState?.summary || t.dash_default_summary}
          </p>
          {route && (
            <div
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 10,
                padding: '10px 14px',
                borderRadius: 999,
                background: 'var(--accent-bg)',
                border: '1px solid var(--accent-border)',
              }}
            >
              <code
                style={{
                  padding: '2px 8px',
                  borderRadius: 999,
                  background: 'var(--accent-bg)',
                  color: 'var(--accent)',
                }}
              >
                {route.recommended_route}
              </code>
              <span>{routeLabel}</span>
            </div>
          )}
        </div>

        <div
          style={{
            padding: '22px 24px',
            borderRadius: 20,
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'space-between',
          }}
        >
          <div>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)', textTransform: 'uppercase' }}>
              {t.dash_updated}
            </div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>
              {projectState?.updated_at
                ? new Date(projectState.updated_at).toLocaleString()
                : '—'}
            </div>
            {projectState?.last_search?.query && (
              <div style={{ color: 'var(--text-secondary)', marginTop: 8 }}>
                {t.dash_last_search}: {projectState.last_search.query}
                {projectState.last_search.discipline
                  ? ` / ${projectState.last_search.discipline}`
                  : ''}
              </div>
            )}
          </div>
          <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
            <button
              onClick={handleRefresh}
              disabled={loading}
              style={{
                padding: '8px 16px',
                borderRadius: 8,
                border: '1px solid var(--border)',
                background: 'var(--bg-secondary)',
                color: 'var(--text-primary)',
                cursor: 'pointer',
                fontWeight: 500,
              }}
            >
              {loading ? t.dash_refreshing : t.dash_refresh}
            </button>
          </div>
        </div>
      </div>

      {error && (
        <div
          style={{
            padding: '12px 16px',
            borderRadius: 10,
            background: '#fef2f2',
            border: '1px solid #fecaca',
            color: '#dc2626',
          }}
        >
          {error}
        </div>
      )}

      {/* Stats */}
      <StatsCards
        paperCount={summary?.paper_count ?? papers.length}
        verifiedCount={summary?.verified_count ?? 0}
        downloadedCount={summary?.downloaded_count ?? 0}
        currentStage={stageLabel}
      />

      {/* Pipeline + Coverage */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <div
          style={{
            padding: 18,
            borderRadius: 16,
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
          }}
        >
          <h2 style={{ margin: '0 0 14px', fontSize: 14, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            {t.dash_pipeline}
          </h2>
          <StageProgress stageStatus={projectState?.stage_status} />
        </div>

        <div
          style={{
            padding: 18,
            borderRadius: 16,
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
          }}
        >
          <h2 style={{ margin: '0 0 14px', fontSize: 14, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            {t.dash_coverage}
          </h2>
          {summary?.source_counts && Object.keys(summary.source_counts).length > 0 ? (
            <div style={{ display: 'grid', gap: 8 }}>
              {Object.entries(summary.source_counts).map(([source, count]) => (
                <div
                  key={source}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    padding: '8px 12px',
                    borderRadius: 10,
                    background: 'var(--bg-secondary)',
                  }}
                >
                  <span>{source}</span>
                  <strong>{count}</strong>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ color: 'var(--text-tertiary)', padding: '8px 0' }}>
              {t.dash_no_data}
            </div>
          )}
        </div>
      </div>

      {/* Recommendations */}
      {recommendations.length > 0 && (
        <div
          style={{
            padding: 18,
            borderRadius: 16,
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
          }}
        >
          <h2 style={{ margin: '0 0 14px', fontSize: 14, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            {t.dash_recommendations}
          </h2>
          <div style={{ display: 'grid', gap: 8 }}>
            {recommendations.map((rec, i) => (
              <div
                key={i}
                style={{
                  padding: '12px 16px',
                  borderRadius: 12,
                  background: rec.priority === 'high' ? 'rgba(239,68,68,0.06)' : 'var(--bg-secondary)',
                  border: `1px solid ${rec.priority === 'high' ? 'rgba(239,68,68,0.2)' : 'var(--border)'}`,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span
                    style={{
                      padding: '2px 8px',
                      borderRadius: 999,
                      fontSize: 11,
                      fontWeight: 600,
                      background: rec.type === 'warning' ? 'rgba(245,158,11,0.15)' : rec.type === 'action' ? 'rgba(99,102,241,0.12)' : 'var(--bg-secondary)',
                      color: rec.type === 'warning' ? '#f59e0b' : rec.type === 'action' ? 'var(--accent)' : 'var(--text-secondary)',
                    }}
                  >
                    {rec.type}
                  </span>
                  <strong style={{ fontSize: 14 }}>{rec.title}</strong>
                </div>
                <div style={{ color: 'var(--text-secondary)', fontSize: 13, marginTop: 6 }}>
                  {rec.description}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export default Dashboard
