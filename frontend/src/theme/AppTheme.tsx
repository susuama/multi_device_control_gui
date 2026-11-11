import type { ReactNode } from "react";
import { Theme } from "@astryxdesign/core/theme";
import { measurementTheme } from "./generated/measurement";
import "@astryxdesign/core/reset.css";
import "@astryxdesign/core/astryx.css";
import "./generated/measurementTheme.css";
import "./density.css";

export type UiDensity = "compact" | "standard" | "comfortable";
export const DEFAULT_UI_DENSITY: UiDensity = "standard";

/** 設定画面から配色と密度を受け取る、アプリ全体のテーマ境界。 */
export function AppTheme({ mode, density = DEFAULT_UI_DENSITY, children }: {
    mode: "light" | "dark";
    density?: UiDensity;
    children: ReactNode;
}) {
    return (
        <div className="app-theme" data-density={density}>
            <Theme theme={measurementTheme} mode={mode}>{children}</Theme>
        </div>
    );
}
