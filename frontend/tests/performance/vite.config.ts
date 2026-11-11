import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";
import { env } from "node:process";
export default defineConfig({
    root: fileURLToPath(new URL("../../", import.meta.url)), plugins: [react()],
    resolve: { alias: env.PERFORMANCE_PROFILE_RENDER === "1" ? { "react-dom/client": "react-dom/profiling" } : {} },
    build: { outDir: env.PERFORMANCE_BUILD_DIR, emptyOutDir: false,
        rollupOptions: { input: fileURLToPath(new URL("./fixture.html", import.meta.url)) } },
});
