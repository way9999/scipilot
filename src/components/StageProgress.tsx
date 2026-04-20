import { type FC } from 'react'
import { getOrderedStageEntries } from '../types/workbench'
import { useT } from '../i18n/context'

interface StageProgressProps {
  stageStatus?: Record<string, string>
}

const StageProgress: FC<StageProgressProps> = ({ stageStatus }) => {
  const t = useT()
  const entries = getOrderedStageEntries(stageStatus)

  const stageLabel = (stage: string) => {
    switch (stage) {
      case 'focus': return t.stage_focus
      case 'literature': return t.stage_literature
      case 'structure': return t.stage_structure
      case 'writing': return t.stage_writing
      case 'complete': return t.stage_complete
      default: return stage
    }
  }

  const statusColor = (status: string) => {
    switch (status) {
      case 'completed': return { bg: 'rgba(16,185,129,0.14)', text: '#10b981' }
      case 'in_progress': return { bg: 'rgba(245,158,11,0.14)', text: '#f59e0b' }
      default: return { bg: 'rgba(148,163,184,0.16)', text: '#94a3b8' }
    }
  }

  const statusLabel = (status: string) => {
    switch (status) {
      case 'completed': return t.status_completed
      case 'in_progress': return t.status_in_progress
      default: return t.status_pending
    }
  }

  return (
    <div style={{ display: 'grid', gap: 8 }}>
      {entries.map(([stage, status]) => {
        const color = statusColor(status)
        return (
          <div
            key={stage}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '10px 14px',
              borderRadius: 12,
              background: 'var(--bg-secondary)',
              border: '1px solid var(--border)',
            }}
          >
            <span style={{ fontWeight: 500 }}>{stageLabel(stage)}</span>
            <span
              style={{
                padding: '4px 10px',
                borderRadius: 999,
                fontSize: 12,
                fontWeight: 600,
                background: color.bg,
                color: color.text,
              }}
            >
              {statusLabel(status)}
            </span>
          </div>
        )
      })}
    </div>
  )
}

export default StageProgress
