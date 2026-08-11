import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev-mode proxy: `npm run dev` serves the app from Vite but forwards the
// WebSocket to a locally running viz-svc, so frontend work needs no rebuild
// of the Python image.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/ws": { target: "ws://localhost:8004", ws: true },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
