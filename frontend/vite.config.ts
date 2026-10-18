import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the Django API runs on :8000 and Vite proxies /api to it.
// In production Django serves frontend/dist at the site root.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
