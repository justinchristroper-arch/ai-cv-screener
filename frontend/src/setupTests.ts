// Registers jest-dom's matchers (toBeInTheDocument, etc.) with Vitest's expect,
// including their TypeScript declarations.
import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// React Testing Library auto-registers cleanup only when Vitest's globals are
// injected. This project uses explicit imports (`globals: false` in
// vite.config.ts), so cleanup must be wired up by hand — without it, rendered
// components accumulate in document.body across tests in the same file and
// queries start finding duplicates.
afterEach(() => {
  cleanup();
});
