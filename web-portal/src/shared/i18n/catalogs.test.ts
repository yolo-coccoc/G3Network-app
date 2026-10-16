/** The English catalogs mirror the Vietnamese ones key for key (conventions §10). */
import { describe, expect, it } from "vitest";

import { resources } from "./i18n";

/** Flatten a catalog into dotted keys, e.g. `errors.notFound`. */
function keysOf(catalog: object, prefix = ""): string[] {
  return Object.entries(catalog).flatMap(([key, value]) =>
    typeof value === "object" && value !== null
      ? keysOf(value as object, `${prefix}${key}.`)
      : [`${prefix}${key}`],
  );
}

describe("i18n catalogs", () => {
  it.each(Object.keys(resources.vi))(
    "namespace %s has the same keys in en and vi",
    (namespace) => {
      const vi = resources.vi[namespace as keyof typeof resources.vi];
      const en = resources.en[namespace as keyof typeof resources.en];

      expect(keysOf(en).sort()).toEqual(keysOf(vi).sort());
    },
  );

  it("has no empty Vietnamese text", () => {
    const emptyKeys = Object.entries(resources.vi).flatMap(
      ([namespace, catalog]) =>
        keysOf(catalog)
          .filter((key) => {
            const text = key
              .split(".")
              .reduce<unknown>(
                (node, part) => (node as Record<string, unknown>)[part],
                catalog,
              );
            return typeof text !== "string" || text.trim() === "";
          })
          .map((key) => `${namespace}:${key}`),
    );

    expect(emptyKeys).toEqual([]);
  });
});
