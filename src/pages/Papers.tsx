import { type FC, useEffect, useCallback } from 'react'
import { useAppDispatch, useAppSelector } from '../store'
import { fetchPapers, downloadPaper, batchDownloadPapers, batchVerifyPapers } from '../store/papersSlice'
import PaperTable from '../components/PaperTable'
import { useT } from '../i18n/context'

const Papers: FC = () => {
  const dispatch = useAppDispatch()
  const { items: papers, loading, downloadingId, error, batchLoading } = useAppSelector((s) => s.papers)
  const t = useT()

  useEffect(() => {
    dispatch(fetchPapers())
  }, [dispatch])

  const handleDownload = useCallback(
    (recordId: string) => {
      dispatch(downloadPaper(recordId)).then(() => dispatch(fetchPapers()))
    },
    [dispatch]
  )

  return (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>{t.papers_title}</h1>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            onClick={() => {
              const undownloaded = papers
                .filter(p => !p.downloaded && !p.local_path && p.record_id)
                .map(p => p.record_id!)
              if (undownloaded.length > 0) {
                dispatch(batchDownloadPapers(undownloaded)).then(() => dispatch(fetchPapers()))
              }
            }}
            disabled={loading || batchLoading}
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
            {batchLoading ? t.papers_processing : t.papers_batch_download}
          </button>
          <button
            onClick={() => {
              const unverified = papers
                .filter(p => !p.verified && p.record_id)
                .map(p => p.record_id!)
              if (unverified.length > 0) {
                dispatch(batchVerifyPapers(unverified)).then(() => dispatch(fetchPapers()))
              }
            }}
            disabled={loading || batchLoading}
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
            {t.papers_batch_verify}
          </button>
          <button
            onClick={() => dispatch(fetchPapers())}
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
      />
    </div>
  )
}

export default Papers
