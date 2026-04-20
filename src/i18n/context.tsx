import { createContext, useContext, type FC, type ReactNode } from 'react'
import { LOCALES, type Locale, type LocaleMessages } from './locales'

const I18nContext = createContext<LocaleMessages>(LOCALES.en)

export const I18nProvider: FC<{ locale: Locale; children: ReactNode }> = ({ locale, children }) => (
  <I18nContext.Provider value={LOCALES[locale]}>
    {children}
  </I18nContext.Provider>
)

/** Hook to get the translation function. Usage: `const t = useT()` then `t.nav_dashboard` */
export function useT(): LocaleMessages {
  return useContext(I18nContext)
}
