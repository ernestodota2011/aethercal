// Flat ESLint config for the AetherCal calendar JS workspace.
//
// Its load-bearing job is the ARCHITECTURE BOUNDARY that mirrors the Python import-linter
// contract (AetherCal-06 §3, RF-23): `@aethercal/calendar-core` is a headless, TS-pure package
// and must NEVER import React or the React layer. `@aethercal/calendar-react` is the only place
// React lives. This is enforced here (not just by convention) so CI fails a PR that crosses it.
import eslintJs from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [
      "**/node_modules/**",
      "**/dist/**",
      "**/.web/**",
      "scripts/**",
      "*.mjs",
    ],
  },
  eslintJs.configs.recommended,
  ...tseslint.configs.recommended,
  {
    // Boundary rule: the headless core must not depend on React (or the React package).
    files: ["packages/core/**/*.{ts,tsx}"],
    rules: {
      "@typescript-eslint/no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: [
                "react",
                "react/*",
                "react-dom",
                "react-dom/*",
                "@aethercal/calendar-react",
                "@aethercal/calendar-react/*",
              ],
              message:
                "calendar-core is headless (RF-23): no React/react-dom and no dependency on the React layer. Keep rendering in @aethercal/calendar-react.",
            },
          ],
        },
      ],
    },
  },
  {
    // Test files exercise loose shapes; relax a couple of rules that fight test ergonomics.
    files: ["**/__tests__/**/*.{ts,tsx}", "**/*.test.{ts,tsx}"],
    rules: {
      "@typescript-eslint/no-non-null-assertion": "off",
    },
  },
);
