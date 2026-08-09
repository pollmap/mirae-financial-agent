import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  server: {
    proxy: {
      "/qa/api": {
        target: "http://127.0.0.1:8090",
        changeOrigin: false,
      },
    },
  },
});
