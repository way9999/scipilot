import { type FC, useCallback } from 'react'
import { useAppDispatch, useAppSelector } from '../store'
import { searchPapers, fetchPapers } from '../store/papersSlice'
import { fetchDashboard } from '../store/researchSlice'
import SearchBar from '../components/SearchBar'
import PaperTable from '../components/PaperTable'
import { useT } from '../i18n/context'

const Search: FC = () => {
  const dispatch = useAppDispatch()
  const { items: papers, searchLoading, error } = useAppSelector((s) => s.papers)
  const t = useT()

  const handleSearch = useCallback(
    (query: string, discipline: string, download: boolean) => {
      dispatch(searchPapers({ query, discipline, limit: 10, download })).then(() => {
        dispatch(fetchPapers())
        dispatch(fetchDashboard())
      })
    },
    [dispatch]
  )

  return (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 18 }}>
      <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>{t.search_title}</h1>

      <SearchBar onSearch={handleSearch} loading={searchLoading} />

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

      <PaperTable papers={papers} />
    </div>
  )
}

export default Search
