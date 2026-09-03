import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // Downgraded, not silenced. next 16.3 added this rule and it fires on
      // two fetch-on-mount effects in the vectors page (loadAtlas,
      // runNeighborSearch). They are a cascading-render performance concern,
      // not a correctness or security one. Sub-project P5 adopts a
      // data-fetching layer and removes both call sites structurally, which
      // is when this override goes away. Kept as a warning so the debt stays
      // visible until then.
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
