import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // Downgraded, not silenced. next 16.3 added this rule and it fires on
      // six pre-existing state-synchronisation effects (ingest and vectors
      // pages, useSessionUser). They are a cascading-render performance
      // concern, not a correctness or security one, and fixing them properly
      // means reworking UI state flow. Kept visible as warnings so the debt
      // is not lost; see docs/technical/08-development.md.
      "react-hooks/set-state-in-effect": "warn",
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
