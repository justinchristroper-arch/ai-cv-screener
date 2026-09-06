import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/setupTests.ts"],
    // Explicit imports in test files rather than injected globals: it keeps
    // the type setup simple and makes each test file self-describing.
    globals: false,
    css: false,
  },
});
