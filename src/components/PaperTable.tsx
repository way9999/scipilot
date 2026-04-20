import { type FC, useState, useMemo } from 'react'
import type { ResearchWorkbenchPaper } from '../types/workbench'
import { isDownloaded } from '../types/workbench'
import { useT } from '../i18n/context'

interface PaperTableProps {
  papers: ResearchWorkbenchPaper[]
  onDownload?: (recordId: string) => void
  downloadingId?: string | null
}

const PaperTable: FC<PaperTableProps> = ({ papers, onDownload, downloadingId }) => {
  const [filter, setFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState<'all' | 'verified' | 'downloaded'>('all')
  const t = useT()

  const filtered = useMemo(() => {
    const q = filter.toLowerCase()
    return papers.filter((p) => {
      const haystack = [p.title, ...(p.authors ?? []), p.venue, p.doi]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      const matchesQuery = !q || haystack.includes(q)
      const matchesStatus =
        statusFilter === 'all' ||
        (statusFilter === 'verified' && p.verified) ||
        (statusFilter === 'downloaded' && isDownloaded(p))
      return matchesQuery && matchesStatus
    })
  }, [papers, filter, statusFilter])

  const headers = [t.papers_col_title, t.papers_col_year, t.papers_col_source, t.papers_col_status, t.papers_col_actions]

  return (
    <div
      style={{
        padding: 18,
        borderRadius: 16,
        background: 'var(--bg-primary)',
        border: '1px solid var(--border)',
      }}
    >
      <div style={{ display: 'flex', gap: 12, marginBottom: 14 }}>
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder={t.papers_filter}
          style={{
            flex: 1,
            padding: '8px 12px',
            borderRadius: 8,
            border: '1px solid var(--border)',
            background: 'var(--bg-secondary)',
            color: 'var(--text-primary)',
            fontSize: 14,
            outline: 'none',
          }}
        />
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}
          style={{
            padding: '8px 12px',
            borderRadius: 8,
            border: '1px solid var(--border)',
            background: 'var(--bg-secondary)',
            color: 'var(--text-primary)',
            fontSize: 14,
          }}
        >
          <option value="all">{t.papers_all}</option>
          <option value="verified">{t.papers_verified}</option>
          <option value="downloaded">{t.papers_downloaded}</option>
        </select>
      </div>

      <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 8 }}>
        {filtered.length} / {papers.length} {t.stats_papers.toLowerCase()}
      </div>

      <div style={{ overflowX: 'auto', borderRadius: 12, border: '1px solid var(--border)' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              {headers.map((h) => (
                <th
                  key={h}
                  style={{
                    padding: '10px 14px',
                    textAlign: 'left',
                    fontSize: 12,
                    fontWeight: 700,
                    color: 'var(--text-tertiary)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.08em',
                    background: 'var(--bg-secondary)',
                    borderBottom: '1px solid var(--border)',
                  }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 200).map((paper) => (
              <tr
                key={paper.record_id ?? `${paper.title}-${paper.year}`}
                style={{ borderBottom: '1px solid var(--border)' }}
              >
                <td style={{ padding: '12px 14px', minWidth: 260 }}>
                  <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
                    {paper.title || t.papers_untitled}
                  </div>
                  <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 4 }}>
                    {paper.authors?.join(', ') || t.papers_unknown_authors}
                  </div>
                  <div style={{ display: 'flex', gap: 4, marginTop: 6, flexWrap: 'wrap' }}>
                    {paper.venue && <Chip>{paper.venue}</Chip>}
                    {paper.discipline && <Chip>{paper.discipline}</Chip>}
                  </div>
                </td>
                <td style={{ padding: '12px 14px', color: 'var(--text-secondary)' }}>
                  {paper.year || 'n/a'}
                </td>
                <td style={{ padding: '12px 14px', color: 'var(--text-secondary)' }}>
                  {paper.source || 'unknown'}
                </td>
                <td style={{ padding: '12px 14px' }}>
                  <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                    {paper.verified && <Chip color="#10b981">{t.papers_verified}</Chip>}
                    {isDownloaded(paper) && <Chip color="#3b82f6">{t.papers_downloaded}</Chip>}
                    {!paper.verified && !isDownloaded(paper) && <Chip>{t.papers_indexed}</Chip>}
                  </div>
                </td>
                <td style={{ padding: '12px 14px' }}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    {paper.url && (
                      <a
                        href={paper.url}
                        target="_blank"
                        rel="noreferrer"
                        style={{ color: 'var(--accent)', fontSize: 13 }}
                      >
                        {t.papers_source}
                      </a>
                    )}
                    {!paper.local_path && paper.record_id && onDownload && (
                      <button
                        onClick={() => onDownload(paper.record_id!)}
                        disabled={downloadingId === paper.record_id}
                        style={{
                          background: 'none',
                          border: 'none',
                          color: 'var(--accent)',
                          cursor: 'pointer',
                          padding: 0,
                          fontSize: 13,
                          textAlign: 'left',
                        }}
                      >
                        {downloadingId === paper.record_id ? t.papers_downloading : t.papers_download}
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length === 0 && (
          <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-tertiary)' }}>
            {t.papers_no_found}
          </div>
        )}
      </div>
    </div>
  )
}

const Chip: FC<{ children: React.ReactNode; color?: string }> = ({ children, color }) => (
  <span
    style={{
      padding: '3px 8px',
      borderRadius: 999,
      fontSize: 11,
      fontWeight: 500,
      background: color ? `${color}22` : 'var(--bg-secondary)',
      color: color || 'var(--text-secondary)',
    }}
  >
    {children}
  </span>
)

export default PaperTable
