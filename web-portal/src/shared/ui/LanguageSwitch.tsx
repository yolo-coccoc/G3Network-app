/**
 * The language switch of the header: Vietnamese or English. The choice is a
 * UI preference (`languageStore`); the app applies it to i18next and to Ant
 * Design's locale.
 */
import { Segmented } from "antd";
import { useTranslation } from "react-i18next";

import { LANGUAGES, type Language } from "../i18n/i18n";
import { useLanguageStore } from "../i18n/languageStore";

/** Short labels shown in the switch. */
const LANGUAGE_SHORT_LABELS: Record<Language, string> = { vi: "VI", en: "EN" };

/** VI | EN switch. */
export function LanguageSwitch() {
  const { t } = useTranslation();
  const language = useLanguageStore((state) => state.language);
  const setLanguage = useLanguageStore((state) => state.setLanguage);

  return (
    <Segmented<Language>
      size="small"
      value={language}
      options={LANGUAGES.map((option) => ({
        value: option,
        label: LANGUAGE_SHORT_LABELS[option],
        title: t(`language.${option}`),
      }))}
      onChange={setLanguage}
    />
  );
}
