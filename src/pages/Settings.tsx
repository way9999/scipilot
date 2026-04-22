import { type FC, useEffect, useMemo, useState } from 'react'
import { useAppDispatch, useAppSelector } from '../store'
import { saveSettings, setSettings } from '../store/settingsSlice'
import { DISCIPLINE_OPTIONS } from '../types/workbench'
import { useT } from '../i18n/context'
import * as api from '../lib/tauri'

type UpdateStatus = 'idle' | 'checking' | 'available' | 'up_to_date' | 'downloading' | 'error'

type ConfigSlot = 'llm' | 'image_gen' | 'ollama'

type SlotConfig = {
  apiKey: string
  baseUrl: string
  model: string
}

const MODEL_SLOTS: Array<{
  key: ConfigSlot
  title: string
  needsKey: boolean
  baseUrlPlaceholder: string
  modelSuggestions: string[]
}> = [
  {
    key: 'llm',
    title: 'LLM 大模型',
    needsKey: true,
    baseUrlPlaceholder: 'https://api.openai.com/v1/chat/completions',
    modelSuggestions: ['gpt-4o', 'gpt-4.1', 'claude-sonnet-4-6'],
  },
  {
    key: 'image_gen',
    title: '生图大模型',
    needsKey: true,
    baseUrlPlaceholder: 'https://api.openai.com/v1',
    modelSuggestions: ['gpt-image-1', 'nano-banana-2', 'grok-imagine-1.0'],
  },
  {
    key: 'ollama',
    title: 'Ollama',
    needsKey: false,
    baseUrlPlaceholder: 'http://localhost:11434/api/chat',
    modelSuggestions: ['qwen2.5', 'llama3.1', 'deepseek-r1'],
  },
]

const RESEARCH_API_KEYS = [
  {
    key: 's2',
    label: 'Semantic Scholar API Key',
    desc: '可免费申请。获取 Key 后可提升请求配额。',
  },
]

const Settings: FC = () => {
  const dispatch = useAppDispatch()
  const { settings } = useAppSelector((s) => s.settings)
  const [projectRoot, setProjectRoot] = useState('')
  const [saved, setSaved] = useState<string | null>(null)
  const [slotConfigs, setSlotConfigs] = useState<Record<ConfigSlot, SlotConfig>>({
    llm: { apiKey: '', baseUrl: '', model: '' },
    image_gen: { apiKey: '', baseUrl: '', model: '' },
    ollama: { apiKey: '', baseUrl: '', model: '' },
  })
  const [testingSlot, setTestingSlot] = useState<ConfigSlot | null>(null)
  const [testResults, setTestResults] = useState<Record<string, { success: boolean; message: string }>>({})
  const [updateStatus, setUpdateStatus] = useState<UpdateStatus>('idle')
  const [updateInfo, setUpdateInfo] = useState<api.UpdateInfo | null>(null)
  const [updateProgress, setUpdateProgress] = useState<api.UpdateProgress>({ downloaded: 0 })
  const [appVersion, setAppVersion] = useState('')
  const [licenseKey, setLicenseKey] = useState('')
  const [licenseStatus, setLicenseStatus] = useState<api.LicenseStatus | null>(null)
  const [licenseActivating, setLicenseActivating] = useState(false)
  const t = useT()

  const runUpdateCheck = () => {
    setUpdateStatus('checking')
    api.checkForUpdates().then((info) => {
      setUpdateInfo(info)
      if (info.available) {
        setUpdateStatus('available')
      } else if (info.error) {
        setUpdateStatus('error')
      } else {
        setUpdateStatus('up_to_date')
      }
    }).catch((error) => {
      setUpdateInfo({
        available: false,
        error: error instanceof Error ? error.message : String(error),
      })
      setUpdateStatus('error')
    })
  }

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

  useEffect(() => {
    api.getProjectRoot().then(setProjectRoot).catch(() => {})
    api.getAppVersion().then(setAppVersion).catch(() => {})
    runUpdateCheck()
    // Load license status
    api.getLicenseStatus().then(setLicenseStatus).catch(() => {})
  }, [])

  useEffect(() => {
    setSlotConfigs({
      llm: {
        apiKey: '',
        baseUrl: settings.api_base_urls?.llm || '',
        model: settings.llm_model || MODEL_SLOTS[0].modelSuggestions[0],
      },
      image_gen: {
        apiKey: '',
        baseUrl: settings.api_base_urls?.image_gen || '',
        model: settings.image_gen_model || MODEL_SLOTS[1].modelSuggestions[0],
      },
      ollama: {
        apiKey: '',
        baseUrl: settings.api_base_urls?.ollama || '',
        model: settings.ollama_model || MODEL_SLOTS[2].modelSuggestions[0],
      },
    })
  }, [settings.api_base_urls, settings.llm_model, settings.image_gen_model, settings.ollama_model])

  const flashSaved = (key: string) => {
    setSaved(key)
    setTimeout(() => setSaved(null), 5000)
  }

  const buildNormalizedSettings = (next?: Partial<typeof settings>) => {
    const merged = { ...settings, ...next }
    const defaultModel = merged.default_provider === 'ollama' ? merged.ollama_model : merged.llm_model
    return { ...merged, default_model: defaultModel }
  }

  const handleSaveDefaults = () => {
    const normalized = buildNormalizedSettings()
    dispatch(setSettings(normalized))
    dispatch(saveSettings(normalized))
    flashSaved('general')
  }

  const handleSaveSlot = async (slot: ConfigSlot) => {
    const config = slotConfigs[slot]
    if (!config) return

    if (config.apiKey.trim()) {
      await api.setApiKey(slot, config.apiKey.trim())
    }

    const next = buildNormalizedSettings({
      api_base_urls: {
        ...settings.api_base_urls,
        [slot]: config.baseUrl.trim(),
      },
      ...(slot === 'llm' ? { llm_model: config.model.trim() } : {}),
      ...(slot === 'image_gen' ? { image_gen_model: config.model.trim() } : {}),
      ...(slot === 'ollama' ? { ollama_model: config.model.trim() } : {}),
    })

    dispatch(setSettings(next))
    dispatch(saveSettings(next))
    setSlotConfigs((prev) => ({
      ...prev,
      [slot]: { ...prev[slot], apiKey: '' },
    }))
    flashSaved(slot)
  }

  const handleTestSlot = async (slot: ConfigSlot) => {
    setTestingSlot(slot)
    setTestResults((prev) => ({
      ...prev,
      [slot]: { success: false, message: t.settings_testing || 'Testing...' },
    }))

    try {
      const config = slotConfigs[slot]
      const result = await api.testLlmConnection(slot, config.model || MODEL_SLOTS.find((item) => item.key === slot)?.modelSuggestions[0] || '')
      setTestResults((prev) => ({
        ...prev,
        [slot]: {
          success: result.success,
          message: result.message || (result.success ? (t.settings_test_success || 'Connection successful!') : (t.settings_test_failed || 'Connection failed')),
        },
      }))
    } catch (error) {
      setTestResults((prev) => ({
        ...prev,
        [slot]: {
          success: false,
          message: `${t.settings_test_failed || 'Connection failed'}: ${error}`,
        },
      }))
    } finally {
      setTestingSlot(null)
    }
  }

  const updateSlotConfig = (slot: ConfigSlot, field: keyof SlotConfig, value: string) => {
    setSlotConfigs((prev) => ({
      ...prev,
      [slot]: { ...prev[slot], [field]: value },
    }))
  }

  const updateProgressPercent = useMemo(() => {
    if (!updateProgress.total || updateProgress.total <= 0) {
      return 0
    }
    return Math.min(100, Math.round((updateProgress.downloaded / updateProgress.total) * 100))
  }, [updateProgress.downloaded, updateProgress.total])

  return (
    <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 24, maxWidth: 900 }}>
      <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>{t.settings_title}</h1>

      <Section title={t.settings_language}>
        <div style={{ display: 'flex', gap: 8 }}>
          {(['zh', 'en'] as const).map((lang) => (
            <button
              key={lang}
              onClick={() => dispatch(setSettings({ language: lang }))}
              style={{
                padding: '10px 24px',
                borderRadius: 10,
                border: `2px solid ${settings.language === lang ? 'var(--accent)' : 'var(--border)'}`,
                background: settings.language === lang ? 'var(--accent-bg)' : 'var(--bg-secondary)',
                color: settings.language === lang ? 'var(--accent)' : 'var(--text-primary)',
                fontWeight: settings.language === lang ? 700 : 400,
                cursor: 'pointer',
                fontSize: 14,
                transition: 'all 0.15s',
              }}>
              {lang === 'zh' ? t.settings_lang_zh : t.settings_lang_en}
            </button>
          ))}
        </div>
      </Section>

      <Section title={t.settings_project}>
        <div style={{ color: 'var(--text-secondary)', fontSize: 14 }}>
          {t.settings_project_root}: <code style={{ color: 'var(--text-primary)' }}>{projectRoot}</code>
        </div>
      </Section>

      <Section title={t.settings_defaults || 'Default Settings'}>
        <Label>{t.settings_provider}</Label>
        <select
          value={settings.default_provider}
          onChange={(e) => dispatch(setSettings(buildNormalizedSettings({ default_provider: e.target.value as 'llm' | 'ollama' })))}
          style={inputStyle}>
          <option value="llm">LLM 大模型</option>
          <option value="ollama">Ollama</option>
        </select>

        <Label>{t.settings_discipline}</Label>
        <select
          value={settings.default_discipline}
          onChange={(e) => dispatch(setSettings({ default_discipline: e.target.value }))}
          style={inputStyle}>
          {DISCIPLINE_OPTIONS.map((discipline) => (
            <option key={discipline.value} value={discipline.value}>
              {disciplineLabels[discipline.value] || discipline.label}
            </option>
          ))}
        </select>

        {saved === 'general' && (
          <Notice success>
            <span>{t.settings_saved}</span>
            <button onClick={() => setSaved(null)} style={closeButtonStyle}>×</button>
          </Notice>
        )}

        <button onClick={handleSaveDefaults} style={buttonStyle}>{t.settings_save}</button>
      </Section>

      <Section title="科研工具 API Keys">
        <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 4 }}>
          配置学术数据库 API Key，用于提升检索速度和配额。
        </div>
        {RESEARCH_API_KEYS.map((item) => (
          <div key={item.key} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <Label>{item.label}</Label>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{item.desc}</div>
            <div style={{ display: 'flex', gap: 8 }}>
              <input
                type="password"
                defaultValue={settings.api_keys?.[item.key] || ''}
                placeholder="输入 API Key..."
                id={`research-key-${item.key}`}
                style={{ ...inputStyle, flex: 1 }}
              />
              <button
                onClick={async () => {
                  const element = document.getElementById(`research-key-${item.key}`) as HTMLInputElement
                  if (element?.value.trim()) {
                    await api.setApiKey(item.key, element.value.trim())
                    flashSaved(item.key)
                    element.value = ''
                  }
                }}
                style={buttonStyle}>
                保存
              </button>
            </div>
            {saved === item.key && <Notice success>已保存（重启应用后生效）</Notice>}
          </div>
        ))}
      </Section>

      {/* AI Coding Agent */}
      <Section title={t.settings_agent_title}>
        <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 8 }}>
          {t.settings_agent_desc}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
          <label style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
            {t.settings_agent_enabled}
          </label>
          <button
            onClick={() => dispatch(setSettings({ agent_enabled: !settings.agent_enabled }))}
            style={{
              width: 44, height: 24, borderRadius: 12, border: 'none', cursor: 'pointer',
              background: settings.agent_enabled ? 'var(--accent)' : 'var(--border)',
              position: 'relative', transition: 'background 0.2s',
            }}>
            <div style={{
              width: 18, height: 18, borderRadius: 9, background: '#fff',
              position: 'absolute', top: 3, left: settings.agent_enabled ? 23 : 3,
              transition: 'left 0.2s', boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
            }} />
          </button>
        </div>

        {settings.agent_enabled && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', gap: 12, alignItems: 'end' }}>
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6 }}>
                <Label>{t.settings_agent_type}</Label>
                <select
                  value={settings.agent_type}
                  onChange={(e) => dispatch(setSettings({ agent_type: e.target.value as 'claude_code' | 'codex' | 'custom' }))}
                  style={inputStyle}>
                  <option value="claude_code">{t.settings_agent_type_claude}</option>
                  <option value="codex">{t.settings_agent_type_codex}</option>
                  <option value="custom">{t.settings_agent_type_custom}</option>
                </select>
              </div>
              <div style={{ flex: 2, display: 'flex', flexDirection: 'column', gap: 6 }}>
                <Label>{t.settings_agent_path}</Label>
                <div style={{ display: 'flex', gap: 8 }}>
                  <input
                    value={settings.agent_path}
                    onChange={(e) => dispatch(setSettings({ agent_path: e.target.value }))}
                    placeholder={t.settings_agent_path_placeholder}
                    style={{ ...inputStyle, flex: 1 }}
                  />
                  <button
                    onClick={async () => {
                      const detected = await api.detectAgentCli(settings.agent_type)
                      if (detected) {
                        dispatch(setSettings({ agent_path: detected }))
                      } else {
                        alert(t.settings_agent_not_found)
                      }
                    }}
                    style={{ ...buttonStyle, whiteSpace: 'nowrap' as const }}>
                    {t.settings_agent_detect}
                  </button>
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', gap: 12 }}>
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6 }}>
                <Label>{t.settings_agent_max_turns}</Label>
                <input
                  type="number" min={1} max={50}
                  value={settings.agent_max_turns}
                  onChange={(e) => dispatch(setSettings({ agent_max_turns: Number(e.target.value) || 10 }))}
                  style={inputStyle}
                />
              </div>
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6 }}>
                <Label>{t.settings_agent_timeout}</Label>
                <input
                  type="number" min={30} max={1800} step={30}
                  value={settings.agent_timeout_secs}
                  onChange={(e) => dispatch(setSettings({ agent_timeout_secs: Number(e.target.value) || 300 }))}
                  style={inputStyle}
                />
              </div>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <input
                  type="checkbox"
                  checked={settings.agent_auto_fix}
                  onChange={(e) => dispatch(setSettings({ agent_auto_fix: e.target.checked }))}
                  style={{ width: 16, height: 16, cursor: 'pointer' }}
                />
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{t.settings_agent_auto_fix}</div>
                  <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{t.settings_agent_auto_fix_desc}</div>
                </div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <input
                  type="checkbox"
                  checked={settings.agent_auto_supplement}
                  onChange={(e) => dispatch(setSettings({ agent_auto_supplement: e.target.checked }))}
                  style={{ width: 16, height: 16, cursor: 'pointer' }}
                />
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{t.settings_agent_auto_supplement}</div>
                  <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{t.settings_agent_auto_supplement_desc}</div>
                </div>
              </div>
            </div>
          </div>
        )}
      </Section>

      {/* Software Update */}
      <Section title={t.settings_update_title}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
            {t.settings_update_current_version}: <code style={{ color: 'var(--text-primary)' }}>{appVersion || '—'}</code>
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button onClick={runUpdateCheck} style={buttonStyle}>
              {t.settings_update_check_btn}
            </button>
            {updateInfo?.available && updateInfo.downloadUrl && updateInfo.source === 'manifest' && (
              <button
                onClick={() => api.downloadAndInstallUpdate()}
                style={{ ...buttonStyle, background: 'var(--surface-secondary)' }}>
                {t.settings_update_download_btn}
              </button>
            )}
          </div>
          {updateStatus === 'checking' && (
            <div style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>{t.settings_update_checking}</div>
          )}
          {updateStatus === 'up_to_date' && (
            <div style={{ fontSize: 13, color: '#16a34a' }}>{t.settings_update_up_to_date}</div>
          )}
          {updateStatus === 'error' && (
            <>
              <div style={{ fontSize: 13, color: '#dc2626', fontWeight: 600 }}>{t.settings_update_error}</div>
              {updateInfo?.error && (
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)', wordBreak: 'break-all' }}>
                  {updateInfo.error}
                </div>
              )}
            </>
          )}
          {updateStatus === 'available' && updateInfo && (
            <>
              <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                {t.settings_update_latest_version}: <code style={{ color: '#16a34a', fontWeight: 600 }}>{updateInfo.version}</code>
              </div>
              <div style={{ fontSize: 13, color: '#16a34a', fontWeight: 500 }}>{t.settings_update_available}</div>
              {updateInfo.source === 'manifest' && (
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{t.settings_update_manifest_fallback}</div>
              )}
              {updateInfo.source !== 'manifest' && (
                <button
                  onClick={async () => {
                    setUpdateStatus('downloading')
                    setUpdateProgress({ downloaded: 0 })
                    const success = await api.downloadAndInstallUpdate((progress) => {
                      setUpdateProgress(progress)
                    })
                    if (!success) {
                      setUpdateStatus('available')
                    }
                  }}
                  style={{ ...buttonStyle, marginTop: 8 }}>
                  {t.settings_update_btn}
                </button>
              )}
            </>
          )}
          {updateStatus === 'downloading' && (
            <>
              <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{t.settings_update_downloading}</div>
              <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
                {t.settings_update_progress}: {(updateProgress.downloaded / 1024 / 1024).toFixed(1)} MB
                {updateProgress.total ? ` / ${(updateProgress.total / 1024 / 1024).toFixed(1)} MB (${updateProgressPercent}%)` : ''}
              </div>
              <div style={{ width: '100%', height: 6, borderRadius: 3, background: 'var(--border)', overflow: 'hidden' }}>
                <div style={{ width: `${updateProgressPercent}%`, height: '100%', background: 'var(--accent)', transition: 'width 0.2s' }} />
              </div>
            </>
          )}
        </div>
      </Section>

      {/* License */}
      <Section title={t.settings_license_title}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {licenseStatus?.valid ? (
            <>
              <div style={{ fontSize: 13, color: '#16a34a', fontWeight: 600 }}>
                ✅ {t.settings_license_activated} — {licenseStatus.tier === 'Student' ? t.settings_license_tier_student : licenseStatus.tier === 'Pro' ? t.settings_license_tier_pro : t.settings_license_tier_free}
              </div>
              <button
                onClick={async () => {
                  await api.deactivateLicense()
                  setLicenseStatus({ valid: false, tier: 'Free' })
                }}
                style={{ ...buttonStyle, background: 'var(--bg-secondary)', color: 'var(--text-primary)', border: '1px solid var(--border)' }}>
                {t.settings_license_deactivate}
              </button>
            </>
          ) : (
            <>
              <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                {t.settings_license_tier_free}
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <input
                  value={licenseKey}
                  onChange={(e) => setLicenseKey(e.target.value.toUpperCase())}
                  placeholder={t.settings_license_key_placeholder}
                  style={{ ...inputStyle, flex: 1 }}
                />
                <button
                  onClick={async () => {
                    if (!licenseKey.trim()) return
                    setLicenseActivating(true)
                    try {
                      const status = await api.activateLicense(licenseKey.trim())
                      setLicenseStatus(status)
                      if (status.valid) {
                        setLicenseKey('')
                      }
                    } catch {
                      // invalid key
                    }
                    setLicenseActivating(false)
                  }}
                  disabled={licenseActivating}
                  style={buttonStyle}>
                  {licenseActivating ? t.settings_license_activating : t.settings_license_activate}
                </button>
              </div>
            </>
          )}
        </div>
      </Section>

      {MODEL_SLOTS.map((slot) => (
        <Section
          key={slot.key}
          title={slot.title + ((slot.key === settings.default_provider || (slot.key === 'llm' && settings.default_provider === 'llm')) ? ' ★' : '')}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <Label>{t.settings_base_url || 'API Base URL'}</Label>
              <input
                type="text"
                value={slotConfigs[slot.key]?.baseUrl || ''}
                onChange={(e) => updateSlotConfig(slot.key, 'baseUrl', e.target.value)}
                style={inputStyle}
                placeholder={slot.baseUrlPlaceholder}
              />
            </div>

            {slot.needsKey && (
              <div>
                <Label>{t.settings_api_key}</Label>
                <input
                  type="password"
                  value={slotConfigs[slot.key]?.apiKey || ''}
                  onChange={(e) => updateSlotConfig(slot.key, 'apiKey', e.target.value)}
                  style={inputStyle}
                  placeholder="留空则不改动已保存的密钥"
                />
              </div>
            )}

            <div>
              <Label>{t.settings_model}</Label>
              <input
                type="text"
                list={`model-suggestions-${slot.key}`}
                value={slotConfigs[slot.key]?.model || ''}
                onChange={(e) => updateSlotConfig(slot.key, 'model', e.target.value)}
                style={inputStyle}
                placeholder={slot.modelSuggestions[0] || 'Enter model name'}
              />
              <datalist id={`model-suggestions-${slot.key}`}>
                {slot.modelSuggestions.map((model) => (
                  <option key={model} value={model}>{model}</option>
                ))}
              </datalist>
              <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 6 }}>
                保存后 API 地址和模型会继续显示；API Key 不回显。
              </div>
            </div>

            {testResults[slot.key] && (
              <Notice success={testResults[slot.key].success}>
                <span>{testResults[slot.key].message}</span>
                <button
                  onClick={() => setTestResults((prev) => {
                    const next = { ...prev }
                    delete next[slot.key]
                    return next
                  })}
                  style={closeButtonStyle}>
                  ×
                </button>
              </Notice>
            )}

            {saved === slot.key && (
              <Notice success>
                <span>{t.settings_saved}</span>
                <button onClick={() => setSaved(null)} style={closeButtonStyle}>×</button>
              </Notice>
            )}

            <div style={{ display: 'flex', gap: 12, marginTop: 4 }}>
              <button onClick={() => void handleSaveSlot(slot.key)} style={buttonStyle}>{t.settings_save}</button>
              <button
                onClick={() => void handleTestSlot(slot.key)}
                disabled={testingSlot === slot.key}
                style={{
                  ...buttonStyle,
                  background: 'var(--bg-secondary)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border)',
                  opacity: testingSlot === slot.key ? 0.6 : 1,
                }}>
                {testingSlot === slot.key ? (t.settings_testing || 'Testing...') : (t.settings_test || 'Test Connection')}
              </button>
            </div>
          </div>
        </Section>
      ))}
    </div>
  )
}

const Section: FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <div style={{ padding: 18, borderRadius: 16, background: 'var(--bg-primary)', border: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: 12 }}>
    <h2 style={{ margin: 0, fontSize: 14, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{title}</h2>
    {children}
  </div>
)

const Notice: FC<{ children: React.ReactNode; success?: boolean }> = ({ children, success = true }) => (
  <div style={{
    padding: '10px 16px',
    borderRadius: 10,
    background: success ? '#f0fdf4' : '#fef2f2',
    border: `1px solid ${success ? '#bbf7d0' : '#fecaca'}`,
    color: success ? '#16a34a' : '#dc2626',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  }}>
    {children}
  </div>
)

const Label: FC<{ children: React.ReactNode }> = ({ children }) => (
  <label style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-secondary)' }}>{children}</label>
)

const inputStyle: React.CSSProperties = {
  padding: '8px 12px',
  borderRadius: 8,
  border: '1px solid var(--border)',
  background: 'var(--bg-secondary)',
  color: 'var(--text-primary)',
  fontSize: 14,
  outline: 'none',
}

const buttonStyle: React.CSSProperties = {
  padding: '10px 20px',
  borderRadius: 8,
  border: 'none',
  background: 'var(--accent)',
  color: '#fff',
  fontWeight: 600,
  cursor: 'pointer',
  alignSelf: 'flex-start',
  marginTop: 4,
}

const closeButtonStyle: React.CSSProperties = {
  background: 'none',
  border: 'none',
  color: 'inherit',
  cursor: 'pointer',
  fontSize: 18,
  padding: 0,
}

export default Settings
