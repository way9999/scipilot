import { type FC, useState } from 'react'
import { DISCIPLINE_OPTIONS } from '../types/workbench'
import { useT } from '../i18n/context'

interface SearchBarProps {
  onSearch: (query: string, discipline: string, download: boolean) => void
  loading?: boolean
}

const SearchBar: FC<SearchBarProps> = ({ onSearch, loading }) => {
  const [query, setQuery] = useState('')
  const [discipline, setDiscipline] = useState('generic')
  const t = useT()

  const disciplineLabels: Record<string, string> = {
    generic: t.discipline_generic,
    cs: t.discipline_cs,
    physics: t.discipline_physics,
    bio: t.discipline_bio,
    chemistry: t.discipline_chemistry,
    materials: t.discipline_materials,
    energy: t.discipline_energy,
    economics: t.discipline_economics,
  }

  const handleSearch = (download: boolean) => {
    if (query.trim()) {
      onSearch(query.trim(), discipline, download)
    }
  }

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '1.6fr 180px auto auto',
        gap: 12,
        padding: 18,
        borderRadius: 16,
        background: 'var(--bg-primary)',
        border: '1px solid var(--border)',
      }}
    >
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && handleSearch(false)}
        placeholder={t.search_placeholder}
        style={{
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
        value={discipline}
        onChange={(e) => setDiscipline(e.target.value)}
        style={{
          padding: '8px 12px',
          borderRadius: 8,
          border: '1px solid var(--border)',
          background: 'var(--bg-secondary)',
          color: 'var(--text-primary)',
          fontSize: 14,
        }}
      >
        {DISCIPLINE_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {disciplineLabels[opt.value] || opt.label}
          </option>
        ))}
      </select>
      <button
        onClick={() => handleSearch(false)}
        disabled={loading || !query.trim()}
        style={{
          padding: '8px 20px',
          borderRadius: 8,
          border: 'none',
          background: 'var(--accent)',
          color: '#fff',
          fontWeight: 600,
          cursor: loading ? 'not-allowed' : 'pointer',
          opacity: loading || !query.trim() ? 0.5 : 1,
        }}
      >
        {loading ? t.search_searching : t.search_btn}
      </button>
      <button
        onClick={() => handleSearch(true)}
        disabled={loading || !query.trim()}
        style={{
          padding: '8px 20px',
          borderRadius: 8,
          border: '1px solid var(--border)',
          background: 'var(--bg-secondary)',
          color: 'var(--text-primary)',
          fontWeight: 600,
          cursor: loading ? 'not-allowed' : 'pointer',
          opacity: loading || !query.trim() ? 0.5 : 1,
        }}
      >
        {t.search_and_download}
      </button>
    </div>
  )
}

export default SearchBar
