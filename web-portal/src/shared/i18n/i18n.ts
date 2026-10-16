/**
 * i18n setup of the portal (web-portal-conventions.md §10): react-i18next,
 * Vietnamese by default and always complete, English second.
 *
 * One JSON namespace per feature plus `common`, under `locales/{vi,en}/`. The
 * Vietnamese catalogs also type every `t()` call (`CustomTypeOptions`), so a
 * misspelled key fails `tsc`; `catalogs.test.ts` checks that English has
 * exactly the same keys.
 */
import i18next from "i18next";
import { initReactI18next } from "react-i18next";

import enCommon from "./locales/en/common.json";
import enFleet from "./locales/en/fleet.json";
import enIdentity from "./locales/en/identity.json";
import enTelemetry from "./locales/en/telemetry.json";
import enVehicles from "./locales/en/vehicles.json";
import viCommon from "./locales/vi/common.json";
import viFleet from "./locales/vi/fleet.json";
import viIdentity from "./locales/vi/identity.json";
import viTelemetry from "./locales/vi/telemetry.json";
import viVehicles from "./locales/vi/vehicles.json";

/** Languages the portal offers; the first is the default. */
export const LANGUAGES = ["vi", "en"] as const;

/** A language code of `LANGUAGES`. */
export type Language = (typeof LANGUAGES)[number];

/** Vietnamese is the default and the fallback for a missing English text. */
export const DEFAULT_LANGUAGE: Language = "vi";

/** Every catalog, by language then namespace. */
export const resources = {
  vi: {
    common: viCommon,
    identity: viIdentity,
    vehicles: viVehicles,
    fleet: viFleet,
    telemetry: viTelemetry,
  },
  en: {
    common: enCommon,
    identity: enIdentity,
    vehicles: enVehicles,
    fleet: enFleet,
    telemetry: enTelemetry,
  },
} as const;

declare module "i18next" {
  interface CustomTypeOptions {
    defaultNS: "common";
    resources: (typeof resources)["vi"];
  }
}

/**
 * Initialize i18next once with every catalog bundled (no network loading).
 *
 * @param language - The language to start in (the saved preference).
 * @returns The initialized i18next instance; later calls return it unchanged.
 */
export function initI18n(language: Language): typeof i18next {
  if (!i18next.isInitialized) {
    void i18next.use(initReactI18next).init({
      resources,
      lng: language,
      fallbackLng: DEFAULT_LANGUAGE,
      ns: Object.keys(resources.vi),
      defaultNS: "common",
      interpolation: { escapeValue: false },
      initAsync: false,
    });
  }
  return i18next;
}

/**
 * Narrow any string to a supported language.
 *
 * @param value - A stored or browser-provided language code.
 * @returns The language, or the default when it is not supported.
 */
export function toLanguage(value: unknown): Language {
  return LANGUAGES.find((language) => language === value) ?? DEFAULT_LANGUAGE;
}
