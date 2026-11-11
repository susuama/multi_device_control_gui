import type { LayoutDefinition, PanelDefinition } from "./layout";

const panels: PanelDefinition[] = [
    { id: "P1", viewId: "devices" },
    { id: "P2", viewId: "image" },
    { id: "P3", viewId: "camera-settings" },
    { id: "P4", viewId: "measurement-recording" },
    { id: "P5", viewId: "graph" },
    { id: "P6", viewId: "power-settings" },
];

/** 各レイアウトの葉には、panelsで定義したIDを指定する。 */
export const layouts: LayoutDefinition[] = [
    { id: "single", title: "1パネル", panels: panels.slice(0, 1), layout: "P1" },
    {
        id: "columns", title: "左右2パネル", panels: panels.slice(0, 2),
        layout: { type: "split", direction: "row", children: ["P1", "P2"] },
    },
    {
        id: "rows", title: "上下2パネル", panels: panels.slice(0, 2),
        layout: { type: "split", direction: "column", children: ["P1", "P2"] },
    },
    {
        id: "grid", title: "2行3列", panels,
        layout: {
            type: "split",
            direction: "row",
            splitPercentages: [25, 50, 25],
            children: [
                { type: "split", direction: "column", children: ["P1", "P4"] },
                { type: "split", direction: "column", children: ["P2", "P5"] },
                { type: "split", direction: "column", children: ["P3", "P6"] },
            ],
        },
    },
];
