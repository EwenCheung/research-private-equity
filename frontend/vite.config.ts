import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const api = `http://localhost:${process.env.API_PORT ?? 8002}`;

export default defineConfig({
  plugins: [react()],
  server: { port: Number(process.env.WEB_PORT ?? 5175), strictPort: true, proxy: { "/api": api } },
});
