import { useEffect } from "react";
import { Provider } from "react-redux";
import { store, useAppDispatch, useAppSelector } from "./store";
import { I18nProvider } from "./i18n/context";
import { fetchSettings } from "./store/settingsSlice";
import Router from "./Router";

function AppInner() {
  const dispatch = useAppDispatch()
  const language = useAppSelector((s) => s.settings.settings.language) as 'zh' | 'en'

  useEffect(() => {
    dispatch(fetchSettings())
  }, [dispatch])

  return (
    <I18nProvider locale={language || 'zh'}>
      <Router />
    </I18nProvider>
  )
}

function App() {
  return (
    <Provider store={store}>
      <AppInner />
    </Provider>
  );
}

export default App;
