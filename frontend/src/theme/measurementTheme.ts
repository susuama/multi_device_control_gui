import { defineTheme } from "@astryxdesign/core/theme";
import { neutralTheme } from "@astryxdesign/theme-neutral/built";

/** 計測画面の共通色。密度に依存する寸法は density.css で切り替える。 */
export const measurementTheme = defineTheme({
    name: "measurement",
    extends: neutralTheme,
    typography: {
        scale: { base: 12, ratio: 1.2 },
        body: { family: "Segoe UI", fallbacks: '"Yu Gothic UI", Meiryo, sans-serif' },
        heading: { family: "Segoe UI", fallbacks: '"Yu Gothic UI", Meiryo, sans-serif' },
        code: { family: "Cascadia Mono", fallbacks: "Consolas, monospace" },
    },
    localTokens: {
        "--app-color-input": ["#ffffff", "#282828"],
        "--app-color-data-surface": ["#fcfcfc", "#202020"],
        "--app-color-text-emphasis": ["#171717", "#ffffff"],
    },
    tokens: {
        "--color-background-body": ["#dadada", "#161616"],
        "--color-background-surface": ["#f3f3f3", "#303030"],
        "--color-background-muted": ["#e8e8e8", "#1d1d1d"],
        "--color-background-card": ["#ebebeb", "#3d3d3d"],
        "--color-neutral": ["#ececec", "#545454"],
        "--color-text-primary": ["#242424", "#e6e6e6"],
        "--color-text-secondary": ["#505050", "#cccccc"],
        "--color-border": ["#b8b8b8", "#3d3d3d"],
        "--color-accent": "#4772b3",
        "--color-text-accent": ["#4772b3", "#8fbeef"],
        "--color-icon-accent": ["#4772b3", "#8fbeef"],
        "--color-on-accent": "#ffffff",
        "--color-info": ["#4772b3", "#8fbeef"],
        "--color-success": ["#28663b", "#91bb9b"],
        "--color-text-success": ["#28663b", "#91bb9b"],
        "--color-error": ["#a53832", "#ed9993"],
        "--color-text-error": ["#a53832", "#ed9993"],
        "--color-warning": ["#806000", "#e5c16d"],
        "--color-text-warning": ["#806000", "#e5c16d"],
    },
});
