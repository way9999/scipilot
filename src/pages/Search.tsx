import { type FC, useCallback } from 'react'
import { useAppDispatch, useAppSelector } from '../store'
import { searchPapers, downloadPaper, crawlPaper } from '../store/papersSlice'
import { fetchDashboard } from '../store/researchSlice'
import { openFileInSystem } from '../lib/tauri'
import SearchBar from '../components/SearchBar'
import PaperTable from '../components/PaperTable'
import { useT } from '../i18n/context'

const Search: FC = () => {
  const dispatch = useAppDispatch()
  const {
    items: papers,
    searchLoading,
    downloadingId,
    crawlingId,
    error,
    searchMeta,
  } = useAppSelector((s) => s.papers)
  const t = useT()

  const handleSearch = useCallback(
    (query: string, discipline: string, download: boolean) => {
      dispatch(searchPapers({ query, discipline, limit: 20, download })).then(() => {
        dispatch(fetchDashboard())
      })
    },
    [dispatch],
  )

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

  return (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 18 }}>
      <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>{t.search_title}</h1>

      <SearchBar onSearch={handleSearch} loading={searchLoading} />

      {searchMeta && (
        <div
          style={{
            display: 'flex',
            gap: 16,
            fontSize: 13,
            color: 'var(--text-secondary)',
            padding: '8px 0',
          }}
        >
          <span>{searchMeta.result_count} results</span>
          {searchMeta.downloaded_count ? (
            <span>{searchMeta.downloaded_count} downloaded</span>
          ) : null}
          {searchMeta.crawled_count ? (
            <span>{searchMeta.crawled_count} text extracted</span>
          ) : null}
          {searchMeta.crawl_failed ? (
            <span style={{ color: '#f59e0b' }}>{searchMeta.crawl_failed} crawl failed</span>
          ) : null}
        </div>
      )}

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

export default Search
