import { type FC } from 'react'
import { useT } from '../i18n/context'

interface StatsCardsProps {
  paperCount: number
  verifiedCount: number
  downloadedCount: number
  currentStage: string
}

const StatsCards: FC<StatsCardsProps> = ({
  paperCount,
  verifiedCount,
  downloadedCount,
  currentStage,
}) => {
  const t = useT()
  const cards = [
    { label: t.stats_papers, value: paperCount },
    { label: t.stats_verified, value: verifiedCount },
    { label: t.stats_downloaded, value: downloadedCount },
    { label: t.stats_stage, value: currentStage },
  ]

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14 }}>
      {cards.map((card) => (
        <div
          key={card.label}
          style={{
            padding: '18px',
            borderRadius: 16,
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
          }}
        >
          <div style={{
            fontSize: 12,
            color: 'var(--text-tertiary)',
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
          }}>
            {card.label}
          </div>
          <div style={{
            marginTop: 8,
            fontSize: typeof card.value === 'number' ? 28 : 18,
            fontWeight: 700,
            color: 'var(--text-primary)',
          }}>
            {card.value}
          </div>
        </div>
      ))}
    </div>
  )
}

export default StatsCards
