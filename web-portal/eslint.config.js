/**
 * ESLint configuration of the portal (web-portal-scaffold.md D8). Part of
 * `make portal-check`, so the pre-commit hook enforces every rule below.
 *
 * Besides the TypeScript and React rules, it encodes the written portal
 * conventions so an agent cannot drift from them silently:
 * - import direction `routes -> features -> shared` (web-portal-conventions.md §5);
 * - the API client and tokens only in `shared/api` (§5, §8);
 * - no user-visible text written in JSX, only i18n keys (§10);
 * - no hand-picked colors, inline styles or CSS files outside `shared/ui`
 *   (web-portal-ui.md §1).
 */
import js from "@eslint/js";
import boundaries from "eslint-plugin-boundaries";
import i18next from "eslint-plugin-i18next";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import { defineConfig, globalIgnores } from "eslint/config";
import globals from "globals";
import tseslint from "typescript-eslint";

/** JSX attributes that display text to the user, checked by `i18next/no-literal-string`. */
const USER_VISIBLE_JSX_ATTRIBUTES = [
  "title",
  "subTitle",
  "label",
  "placeholder",
  "alt",
  "aria-label",
  "tooltip",
  "okText",
  "cancelText",
  "description",
  "message",
  "extra",
  "help",
  "tip",
  "content",
  "emptyText",
  "addonBefore",
  "addonAfter",
  "text",
];

export default defineConfig([
  globalIgnores(["dist/", "packages/api-client/src/schema.d.ts"]),

  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      tseslint.configs.strictTypeChecked,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },

  {
    files: ["**/*.js"],
    extends: [js.configs.recommended],
    languageOptions: { globals: globals.node },
  },

  // Import direction (web-portal-conventions.md §5). Every file under src/
  // belongs to one layer; anything not allowed below is an error.
  {
    files: ["src/**/*.{ts,tsx}"],
    plugins: { boundaries },
    settings: {
      "boundaries/include": ["src/**/*"],
      // The entry file only mounts app/App; it belongs to no layer.
      "boundaries/ignore": ["src/main.tsx"],
      "boundaries/elements": [
        { type: "app", pattern: "src/app" },
        { type: "routes", pattern: "src/routes" },
        {
          type: "feature",
          pattern: "src/features/*",
          capture: ["featureName"],
        },
        { type: "shared", pattern: "src/shared/*", capture: ["sharedArea"] },
      ],
      "import/resolver": {
        node: { extensions: [".ts", ".tsx", ".js"] },
      },
    },
    rules: {
      "boundaries/no-unknown-files": "error",
      "boundaries/dependencies": [
        "error",
        {
          default: "disallow",
          policies: [
            // Files of one element (one feature, one shared area...) import each other freely.
            { allow: { dependency: { relationship: { to: "internal" } } } },
            {
              from: { element: { type: "app" } },
              allow: {
                to: [
                  { element: { type: ["routes", "shared"] } },
                  {
                    element: { type: "feature", fileInternalPath: "index.ts" },
                  },
                ],
              },
            },
            {
              from: { element: { type: "routes" } },
              allow: {
                to: [
                  { element: { type: "shared" } },
                  {
                    element: { type: "feature", fileInternalPath: "index.ts" },
                  },
                ],
              },
            },
            {
              from: { element: { type: "feature" } },
              allow: { to: { element: { type: "shared" } } },
            },
            {
              from: { element: { type: "shared" } },
              allow: { to: { element: { type: "shared" } } },
            },
            // A feature imports another feature only where the backend has the same
            // edge (domain-boundaries.md), and only through its index.ts. Each such
            // edge is added here as its own policy, e.g.
            // { from: { element: { type: "feature", captured: { featureName: "telemetry" } } },
            //   allow: { to: { element: { type: "feature", captured: { featureName: "fleet" } , fileInternalPath: "index.ts" } } } }
          ],
        },
      ],
    },
  },

  // Only shared/api creates the client and handles tokens (§5, §8); everyone
  // else may import the generated types.
  {
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["src/shared/api/**"],
    rules: {
      "@typescript-eslint/no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "@g3/api-client",
              allowTypeImports: true,
              message:
                "Use the client from shared/api; import only types from @g3/api-client.",
            },
            {
              name: "openapi-fetch",
              message: "Only shared/api creates the API client.",
            },
          ],
        },
      ],
    },
  },

  // Styling only through theme tokens and Ant Design props (web-portal-ui.md §1).
  {
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["src/shared/ui/**"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector:
            "Literal[value=/^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/], Literal[value=/^(?:rgb|hsl)a?\\(/]",
          message:
            "No hand-picked colors: use a theme token (shared/ui/theme.ts) or a StatusTag.",
        },
        {
          selector: "JSXAttribute[name.name='style']",
          message:
            "No style props: lay out with Flex, Space, Row/Col, Card (web-portal-ui.md §1).",
        },
      ],
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["*.css", "**/*.css"],
              message:
                "No CSS files outside shared/ui: style through theme tokens.",
            },
          ],
        },
      ],
    },
  },

  // Every user-visible string is an i18n key (web-portal-conventions.md §10).
  {
    files: ["src/**/*.tsx"],
    ignores: ["src/**/*.test.tsx"],
    plugins: { i18next },
    rules: {
      "i18next/no-literal-string": [
        "error",
        {
          mode: "jsx-only",
          "jsx-attributes": { include: USER_VISIBLE_JSX_ATTRIBUTES },
          message:
            "User-visible text must be an i18n key (t('...')), not a literal.",
        },
      ],
    },
  },
]);
