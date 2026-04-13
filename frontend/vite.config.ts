import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const proxyTarget = env.VITE_API_PROXY_TARGET;

  return {
    plugins: [vue()],
    server: {
      host: "0.0.0.0",
      port: 5173,
      proxy: proxyTarget
        ? {
            "/api/v1": {
              target: proxyTarget,
              changeOrigin: true,
            },
          }
        : undefined,
    },
    preview: {
      host: "0.0.0.0",
      port: 4173,
    },
  };
});
