import type { MosaicNode } from "react-mosaic-component";

export type PanelDefinition = { id: string; viewId: string; title?: string };
export type LayoutDefinition = {
    id: string;
    title: string;
    panels: readonly PanelDefinition[];
    layout: MosaicNode<string> | null;
};

/** 配置に含まれるパネルIDを、分割ツリーの順に取得する。 */
export function panelIds(node: MosaicNode<string> | null): string[] {
    if (node === null) return [];
    if (typeof node === "string") return [node];
    if (node.type === "tabs") throw new Error("このワークスペースはタブ配置に対応していません。");
    return node.children.flatMap(panelIds);
}

/** 表示用の最大化ツリーではなく、元の配置から対象パネルの位置を取得する。 */
export function panelPath(node: MosaicNode<string> | null, id: string): number[] | null {
    if (node === null) return null;
    if (typeof node === "string") return node === id ? [] : null;
    if (node.type === "tabs") throw new Error("タブ配置には対応していません。");
    for (const [index, child] of node.children.entries()) {
        const path = panelPath(child, id);
        if (path !== null) return [index, ...path];
    }
    return null;
}

/** 初期配置とビューの参照を検証し、全レイアウトのパネル定義をまとめる。 */
export function validateLayouts(layouts: readonly LayoutDefinition[], viewIds: readonly string[], initialLayoutId: string) {
    const ids = new Set<string>();
    const panels = new Map<string, PanelDefinition>();
    function validateNode(node: MosaicNode<string> | null) {
        if (node === null || typeof node === "string") return;
        if (node.type !== "split") throw new Error("タブ配置には対応していません。");
        if (!["row", "column"].includes(node.direction) || node.children.length < 2) {
            throw new Error("分割には方向と2つ以上の領域が必要です。");
        }
        const sizes = node.splitPercentages;
        if (sizes && (sizes.length !== node.children.length || sizes.some(size => !Number.isFinite(size) || size <= 0)
            || Math.abs(sizes.reduce((total, size) => total + size, 0) - 100) > 0.001)) {
            throw new Error("分割率は領域数と一致し、正数で合計100にしてください。");
        }
        node.children.forEach(validateNode);
    }
    for (const definition of layouts) {
        try {
            if (!definition.id || ids.has(definition.id)) throw new Error("レイアウトIDが空か重複しています。");
            ids.add(definition.id);
            const local = new Set<string>();
            for (const panel of definition.panels) {
                if (!panel.id || local.has(panel.id)) throw new Error(`パネルIDが空か重複しています: ${panel.id}`);
                if (!viewIds.includes(panel.viewId)) throw new Error(`ビューが存在しません: ${panel.viewId}`);
                const previous = panels.get(panel.id);
                if (previous && (previous.viewId !== panel.viewId || previous.title !== panel.title)) {
                    throw new Error(`同じパネルIDの初期定義が異なります: ${panel.id}`);
                }
                local.add(panel.id);
                panels.set(panel.id, panel);
            }
            validateNode(definition.layout);
            const leaves = panelIds(definition.layout);
            if (new Set(leaves).size !== leaves.length) throw new Error("配置内のパネルIDが重複しています。");
            if (leaves.length !== local.size || leaves.some(id => !local.has(id))) {
                throw new Error("配置とパネル一覧のIDが一致しません。");
            }
        } catch (cause) {
            throw new Error(`レイアウト「${definition.id}」: ${cause instanceof Error ? cause.message : String(cause)}`, { cause });
        }
    }
    if (!ids.has(initialLayoutId)) throw new Error(`初期レイアウトが存在しません: ${initialLayoutId}`);
    return [...panels.values()];
}

/** 指定したパネルを分割し、新しいパネルを右または下へ追加する。 */
export function insertPanel(tree: MosaicNode<string> | null, target: string, id: string, direction: "row" | "column"): MosaicNode<string> {
    if (tree === null) return id;
    if (typeof tree === "string") {
        return tree === target ? { type: "split", direction, children: [tree, id] } : tree;
    }
    if (tree.type === "tabs") throw new Error("タブ配置には対応していません。");
    return { ...tree, children: tree.children.map(child => insertPanel(child, target, id, direction)) };
}
