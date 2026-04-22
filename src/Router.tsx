import { type FC, type ReactNode, useEffect, useMemo, useState } from 'react'
import Dashboard from './pages/Dashboard'
import Assistants from './pages/Assistants'
import Search from './pages/Search'
import Papers from './pages/Papers'
import Pipeline from './pages/Pipeline'
import Landscape from './pages/Landscape'
import Writing from './pages/Writing'
import Settings from './pages/Settings'
import GroupChat from './pages/GroupChat'
import { useT } from './i18n/context'
import * as api from './lib/tauri'

type Page =
  | 'dashboard'
  | 'assistants'
  | 'search'
  | 'papers'
  | 'pipeline'
  | 'landscape'
  | 'writing'
  | 'groupchat'
  | 'settings'

const NAV_ICONS: Record<Page, string> = {
  dashboard: '⌂',
  assistants: '◉',
  search: '⌕',
  papers: '▤',
  pipeline: '▸',
  landscape: '◈',
  writing: '✎',
  groupchat: '💬',
  settings: '⚙',
}

const Router: FC = () => {
  const [page, setPage] = useState<Page>('dashboard')
  const [updateInfo, setUpdateInfo] = useState<api.UpdateInfo | null>(null)
  const [updateBusy, setUpdateBusy] = useState(false)
  const [updateProgress, setUpdateProgress] = useState<api.UpdateProgress>({ downloaded: 0 })
  const t = useT()

  useEffect(() => {
    let cancelled = false
    api.checkForUpdates().then((info) => {
      if (!cancelled) {
        setUpdateInfo(info)
      }
    }).catch((error) => {
      if (!cancelled) {
        setUpdateInfo({
          available: false,
          error: error instanceof Error ? error.message : String(error),
        })
      }
    })
    return () => {
      cancelled = true
    }
  }, [])

  const updateProgressPercent = useMemo(() => {
    if (!updateProgress.total || updateProgress.total <= 0) {
      return 0
    }
    return Math.min(100, Math.round((updateProgress.downloaded / updateProgress.total) * 100))
  }, [updateProgress.downloaded, updateProgress.total])

  const handleUpdateNow = async () => {
    setUpdateBusy(true)
    setUpdateProgress({ downloaded: 0 })
    const ok = await api.downloadAndInstallUpdate((progress) => {
      setUpdateProgress(progress)
    })
    if (!ok) {
      setUpdateBusy(false)
      setPage('settings')
      return
    }
  }

  const navLabels: Record<Page, string> = {
    dashboard: t.nav_dashboard,
    assistants: t.nav_assistants,
    search: t.nav_search,
    papers: t.nav_papers,
    pipeline: t.nav_pipeline,
    landscape: t.nav_landscape,
    writing: t.nav_writing,
    groupchat: '群聊',
    settings: t.nav_settings,
  }

  const navItems: Page[] = ['dashboard', 'assistants', 'search', 'papers', 'pipeline', 'landscape', 'writing', 'groupchat', 'settings']

  const pages: { key: Page; component: ReactNode }[] = [
    { key: 'dashboard', component: <Dashboard /> },
    { key: 'assistants', component: <Assistants /> },
    { key: 'search', component: <Search /> },
    { key: 'papers', component: <Papers /> },
    { key: 'pipeline', component: <Pipeline onNavigate={setPage} /> },
    { key: 'landscape', component: <Landscape /> },
    { key: 'writing', component: <Writing onNavigate={setPage} /> },
    { key: 'groupchat', component: <GroupChat /> },
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
          const showUpdateBadge = key === 'settings' && Boolean(updateInfo?.available)
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
              {showUpdateBadge && (
                <div
                  style={{
                    position: 'absolute',
                    right: 6,
                    top: 6,
                    minWidth: 8,
                    height: 8,
                    borderRadius: 999,
                    background: '#ef4444',
                    boxShadow: '0 0 0 2px var(--bg-primary)',
                  }}
                />
              )}
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
        {updateInfo?.available && (
          <div
            style={{
              padding: '10px 20px',
              borderBottom: '1px solid rgba(251, 191, 36, 0.35)',
              background: 'linear-gradient(90deg, rgba(245, 158, 11, 0.16), rgba(251, 191, 36, 0.08))',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 12,
              flexShrink: 0,
            }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
                {t.settings_update_available}: v{updateInfo.version}
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                {updateBusy
                  ? `${t.settings_update_downloading} ${updateProgress.total ? `${updateProgressPercent}%` : ''}`.trim()
                  : (updateInfo.source === 'manifest'
                    ? t.settings_update_manifest_fallback
                    : t.settings_update_latest_version)}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <button
                onClick={() => setPage('settings')}
                style={{
                  padding: '8px 14px',
                  borderRadius: 8,
                  border: '1px solid var(--border)',
                  background: 'var(--bg-primary)',
                  color: 'var(--text-primary)',
                  cursor: 'pointer',
                }}>
                {t.nav_settings}
              </button>
              <button
                onClick={() => void handleUpdateNow()}
                disabled={updateBusy}
                style={{
                  padding: '8px 14px',
                  borderRadius: 8,
                  border: 'none',
                  background: '#f59e0b',
                  color: '#111827',
                  fontWeight: 700,
                  cursor: updateBusy ? 'default' : 'pointer',
                  opacity: updateBusy ? 0.7 : 1,
                }}>
                {updateInfo.source === 'manifest' ? t.settings_update_download_btn : t.settings_update_btn}
              </button>
            </div>
          </div>
        )}

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
