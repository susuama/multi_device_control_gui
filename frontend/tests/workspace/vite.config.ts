import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";
import { env } from "node:process";
import { tmpdir } from "node:os";
import { join } from "node:path";

export default defineConfig({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    plugins: [react()],
    build: {
        outDir: env.WORKSPACE_TEST_BUILD_DIR ?? join(tmpdir(), "multi-device-workspace-fixture-build"),
        emptyOutDir: false,
        rollupOptions: { input: fileURLToPath(new URL("./fixture.html", import.meta.url)) },
    },
});
