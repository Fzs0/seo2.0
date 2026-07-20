import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5174,
    watch: {
      // Y: is a shared/network drive; native fs events are not reliable there.
      usePolling: true,
      interval: 500,
    },
  },
});
