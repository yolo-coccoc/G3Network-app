/**
 * The portal's root (web-portal-conventions.md §4): providers for the theme
 * and locale (Ant Design), feedback (`App` for `message`), server data
 * (TanStack Query) and routing. The language preference drives both i18next
 * and Ant Design's locale.
 */
import "../shared/ui/globalStyles";

import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { App as AntApp, ConfigProvider } from "antd";
import enUS from "antd/locale/en_US";
import viVN from "antd/locale/vi_VN";
import { useEffect } from "react";

import { initI18n } from "../shared/i18n/i18n";
import { useLanguageStore } from "../shared/i18n/languageStore";
import { portalTheme } from "../shared/ui/theme";
import { queryClient } from "./queryClient";
import { router } from "./router";

const i18n = initI18n(useLanguageStore.getState().language);

/** The whole portal. */
export function App() {
  const language = useLanguageStore((state) => state.language);

  useEffect(() => {
    void i18n.changeLanguage(language);
    document.documentElement.lang = language;
  }, [language]);

  return (
    <ConfigProvider
      theme={portalTheme}
      locale={language === "vi" ? viVN : enUS}
    >
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  );
}
