import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "coverage", "node_modules"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: globals.browser,
    },
    // The plugin is registered explicitly rather than via its shipped preset.
    // eslint-plugin-react-hooks 7 exports its presets in eslintrc shape under
    // `configs` (flat ones live under `configs.flat`), and its "recommended"
    // set now pulls in ~30 rules including the experimental React Compiler
    // checks. These two are the ones that catch real bugs — calling a hook
    // conditionally, and stale closures from missing dependencies.
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",

      // The XSS control for untrusted document content, enforced rather than
      // remembered (docs/architecture.md section 13). Every string this app
      // renders — a CV quote, a skill name, a role title, a recruiter's
      // criteria — came from a file someone uploaded or a box someone typed
      // into, and React escapes all of it as long as nobody reaches for the
      // one API that opts out. `no-restricted-syntax` rather than a React
      // plugin rule so it fires on the JSX attribute *and* on a props object
      // built somewhere else and spread in.
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message:
            "dangerouslySetInnerHTML renders untrusted CV and criteria text as HTML. " +
            "Render it as text instead.",
        },
        {
          selector: "Property[key.name='dangerouslySetInnerHTML']",
          message:
            "dangerouslySetInnerHTML renders untrusted CV and criteria text as HTML. " +
            "Render it as text instead.",
        },
      ],
    },
  },
);
