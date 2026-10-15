import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { 
    strictPort: true, 
    port: 5173,
    proxy: {
      "/api": { 
        target: "http://127.0.0.1:8000", 
        changeOrigin: true,
        // Vite 7.x: ensure ALL methods (including POST/PUT) are forwarded rather than
        // hitting the SPA fallback. Returning null/undefined means "do proxy".
        bypass: (req) => {
          if (req.url?.startsWith("/api")) return null;
          return undefined;
        },
      },
      "/ws": { target: "ws://127.0.0.1:8000", ws: true, changeOrigin: true },
    }
  },
  build: {
    outDir: "dist", sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          "vendor-three": ["three", "three/examples/jsm/controls/OrbitControls.js"],
          "vendor-maplibre": ["maplibre-gl"],
          "vendor-react": ["react", "react-dom", "zustand"],
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
