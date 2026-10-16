/**
 * The user's language preference (web-portal-conventions.md §9: Zustand holds
 * UI preferences). Kept in `localStorage` because it is a per-browser
 * convenience; when storage is unavailable the portal simply starts in
 * Vietnamese.
 */
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

import { DEFAULT_LANGUAGE, type Language, toLanguage } from "./i18n";

/**
 * Language preference state.
 *
 * Attributes:
 *   language: The language the portal is shown in.
 *   setLanguage: Change it (the app applies it to i18next and Ant Design).
 */
interface LanguageState {
  language: Language;
  setLanguage: (language: Language) => void;
}

export const useLanguageStore = create<LanguageState>()(
  persist(
    (set) => ({
      language: DEFAULT_LANGUAGE,
      setLanguage: (language) => {
        set({ language });
      },
    }),
    {
      name: "g3.portal.language",
      storage: createJSONStorage(() => localStorage),
      merge: (persisted, current) => ({
        ...current,
        language: toLanguage(
          (persisted as Partial<LanguageState> | undefined)?.language,
        ),
      }),
    },
  ),
);
