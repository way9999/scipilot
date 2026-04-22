import { type FC, useEffect, useCallback } from 'react'
import { useAppDispatch, useAppSelector } from '../store'
import {
  fetchPapers,
  downloadPaper,
  batchDownloadPapers,
  batchVerifyPapers,
  crawlPaper,
  batchCrawlPapers,
} from '../store/papersSlice'
import { openFileInSystem } from '../lib/tauri'
import PaperTable from '../components/PaperTable'
import { useT } from '../i18n/context'

const Papers: FC = () => {
  const dispatch = useAppDispatch()
  const {
    items: papers,
    loading,
    downloadingId,
    crawlingId,
    error,
    batchLoading,
  } = useAppSelector((s) => s.papers)
  const t = useT()

  useEffect(() => {
    dispatch(fetchPapers())
  }, [dispatch])

  const handleDownload = useCallback(
    (recordId: string) => {
      dispatch(downloadPaper(recordId))
    },
    [dispatch],
  )

  const handleCrawl = useCallback(
    (recordId: string) => {
      dispatch(crawlPaper(recordId))
    },
    [dispatch],
  )

  const handleOpenFile = useCallback((relPath: string) => {
    openFileInSystem(relPath).catch(() => {})
  }, [])

  const btnStyle = (disabled?: boolean) => ({
    padding: '8px 16px',
    borderRadius: 8,
    border: '1px solid var(--border)',
    background: 'var(--bg-secondary)',
    color: 'var(--text-primary)',
    cursor: (disabled ? 'default' : 'pointer') as 'default' | 'pointer',
    fontWeight: 500 as const,
  })

  return (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>{t.papers_title}</h1>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button
            onClick={() => {
              const ids = papers
                .filter(p => !p.downloaded && !p.local_path && p.record_id)
                .map(p => p.record_id!)
              if (ids.length > 0) {
                dispatch(batchDownloadPapers(ids)).then(() => dispatch(fetchPapers()))
              }
            }}
            disabled={loading || batchLoading}
            style={btnStyle(loading || batchLoading)}
          >
            {batchLoading ? t.papers_processing : t.papers_batch_download}
          </button>
          <button
            onClick={() => {
              const ids = papers
                .filter(p => !p.verified && p.record_id)
                .map(p => p.record_id!)
              if (ids.length > 0) {
                dispatch(batchVerifyPapers(ids)).then(() => dispatch(fetchPapers()))
              }
            }}
            disabled={loading || batchLoading}
            style={btnStyle(loading || batchLoading)}
          >
            {t.papers_batch_verify}
          </button>
          <button
            onClick={() => {
              const ids = papers
                .filter(p => !p.content_crawled && p.record_id)
                .map(p => p.record_id!)
              if (ids.length > 0) {
                dispatch(batchCrawlPapers(ids)).then(() => dispatch(fetchPapers()))
              }
            }}
            disabled={loading || batchLoading}
            style={btnStyle(loading || batchLoading)}
          >
            {batchLoading ? t.papers_processing : t.papers_batch_crawl}
          </button>
          <button
            onClick={() => dispatch(fetchPapers())}
            disabled={loading}
            style={btnStyle(loading)}
          >
            {loading ? t.papers_loading : t.papers_reload}
          </button>
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

      <PaperTable
        papers={papers}
        onDownload={handleDownload}
        downloadingId={downloadingId}
        onCrawl={handleCrawl}
        crawlingId={crawlingId}
        onOpenFile={handleOpenFile}
      />
    </div>
  )
}

export default Papers
