import { type FC, type ReactNode, useState } from 'react'
import Dashboard from './pages/Dashboard'
import Assistants from './pages/Assistants'
import Search from './pages/Search'
import Papers from './pages/Papers'
import Pipeline from './pages/Pipeline'
import Landscape from './pages/Landscape'
import Writing from './pages/Writing'
import Settings from './pages/Settings'
import { useT } from './i18n/context'

type Page =
  | 'dashboard'
  | 'assistants'
  | 'search'
  | 'papers'
  | 'pipeline'
  | 'landscape'
  | 'writing'
  | 'settings'

const NAV_ICONS: Record<Page, string> = {
  dashboard: '⌂',
  assistants: '◉',
  search: '⌕',
  papers: '▤',
  pipeline: '▸',
  landscape: '◈',
  writing: '✎',
  settings: '⚙',
}

const Router: FC = () => {
  const [page, setPage] = useState<Page>('dashboard')
  const t = useT()

  const navLabels: Record<Page, string> = {
    dashboard: t.nav_dashboard,
    assistants: t.nav_assistants,
    search: t.nav_search,
    papers: t.nav_papers,
    pipeline: t.nav_pipeline,
    landscape: t.nav_landscape,
    writing: t.nav_writing,
    settings: t.nav_settings,
  }

  const navItems: Page[] = ['dashboard', 'assistants', 'search', 'papers', 'pipeline', 'landscape', 'writing', 'settings']

  const pages: { key: Page; component: ReactNode }[] = [
    { key: 'dashboard', component: <Dashboard /> },
    { key: 'assistants', component: <Assistants /> },
    { key: 'search', component: <Search /> },
    { key: 'papers', component: <Papers /> },
    { key: 'pipeline', component: <Pipeline onNavigate={setPage} /> },
    { key: 'landscape', component: <Landscape /> },
    { key: 'writing', component: <Writing onNavigate={setPage} /> },
    { key: 'settings', component: <Settings /> },
  ]

  return (
    <div style={{ display: 'flex', height: '100vh', background: 'var(--bg-base)' }}>
      <nav
        style={{
          width: 64,
          background: 'var(--bg-primary)',
          borderRight: '1px solid var(--border)',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          padding: '12px 0',
          flexShrink: 0,
          gap: 2,
        }}>
        <div
          style={{
            width: 40,
            height: 40,
            borderRadius: 12,
            background: 'var(--accent)',
            color: '#fff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 18,
            fontWeight: 800,
            marginBottom: 16,
            letterSpacing: '-0.04em',
          }}>
          SP
        </div>

        {navItems.map((key) => {
          const isActive = page === key
          return (
            <button
              key={key}
              onClick={() => setPage(key)}
              title={navLabels[key]}
              style={{
                width: 44,
                height: 44,
                borderRadius: 12,
                border: 'none',
                background: isActive ? 'var(--accent-bg)' : 'transparent',
                color: isActive ? 'var(--accent)' : 'var(--text-tertiary)',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 18,
                fontWeight: isActive ? 700 : 400,
                transition: 'all 0.15s',
                position: 'relative',
              }}>
              {NAV_ICONS[key]}
              {isActive && (
                <div
                  style={{
                    position: 'absolute',
                    left: -2,
                    top: '50%',
                    transform: 'translateY(-50%)',
                    width: 4,
                    height: 20,
                    borderRadius: 2,
                    background: 'var(--accent)',
                  }}
                />
              )}
            </button>
          )
        })}

        <div style={{ flex: 1 }} />
        <div
          style={{
            fontSize: 9,
            color: 'var(--text-tertiary)',
            letterSpacing: '0.08em',
            writingMode: 'vertical-lr',
            opacity: 0.5,
          }}>
          SciPilot
        </div>
      </nav>

      <main style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        {page !== 'assistants' && (
          <div
            style={{
              padding: '10px 24px',
              borderBottom: '1px solid var(--border)',
              background: 'var(--bg-primary)',
              fontSize: 13,
              fontWeight: 600,
              color: 'var(--text-secondary)',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              flexShrink: 0,
            }}>
            <span style={{ color: 'var(--accent)' }}>{NAV_ICONS[page]}</span>
            {navLabels[page]}
          </div>
        )}

        <div style={{ flex: 1, overflow: 'auto', position: 'relative' }}>
          {pages.map(({ key, component }) => (
            <div
              key={key}
              style={{
                display: page === key ? 'flex' : 'none',
                flexDirection: 'column',
                height: '100%',
                overflow: 'auto',
              }}>
              {component}
            </div>
          ))}
        </div>
      </main>
    </div>
  )
}

export default Router
