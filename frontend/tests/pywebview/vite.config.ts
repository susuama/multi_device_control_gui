import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { env } from "node:process";

// 検証画面は製品のdistへ混ぜず、一時ディレクトリへビルドする。
export default defineConfig({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    plugins: [react()],
    build: {
        outDir: env.CORE_TEST_BUILD_DIR ?? join(tmpdir(), "multi-device-core-fixture-build"),
        emptyOutDir: false,
        rollupOptions: { input: fileURLToPath(new URL("./fixture.html", import.meta.url)) },
    },
});
