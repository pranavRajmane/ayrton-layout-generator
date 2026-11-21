import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// the layout generator's own UI; its server is layouts.server on port 8010 (the editor's API is not used)
export default defineConfig({
  plugins: [react()],
  server: { port: 5174, proxy: { "/api/layouts": "http://127.0.0.1:8010" } },
});
