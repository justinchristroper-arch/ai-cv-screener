// Registers jest-dom's matchers (toBeInTheDocument, etc.) with Vitest's expect,
// including their TypeScript declarations.
import "@testing-library/jest-dom/vitest";

import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library gives every `findBy*` and `waitFor` one second by default,
// and one second is a *wall-clock* budget shared with every other test file
// vitest is running in parallel. That was already marginal here and became a
// real intermittent failure once the suite reached fourteen files: the job
// screen's first heading would be reported missing after ~2.3s of waiting,
// while the same file passed on its own in 3s total.
//
// The budget is raised rather than sprinkled over individual assertions
// because it is a property of the suite, not of one test. Nothing waits longer
// than it needs to — a passing assertion resolves on the first poll either
// way, so this only changes how patient a *failing* one is.
configure({ asyncUtilTimeout: 5000 });

// React Testing Library auto-registers cleanup only when Vitest's globals are
// injected. This project uses explicit imports (`globals: false` in
// vite.config.ts), so cleanup must be wired up by hand — without it, rendered
// components accumulate in document.body across tests in the same file and
// queries start finding duplicates.
afterEach(() => {
  cleanup();
});
