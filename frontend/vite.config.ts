import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Worktree n uses API 8000+n and Vite 5173+n (CLAUDE.md); main and phase branches are n = 0.
const api = `http://localhost:${process.env.API_PORT ?? 8000}`;

export default defineConfig({
  plugins: [react()],
  server: { port: Number(process.env.WEB_PORT ?? 5173), strictPort: true, proxy: { "/api": api } },
});
