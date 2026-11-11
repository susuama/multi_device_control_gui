import { Activity, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { Mosaic, createRemoveUpdate, updateTree } from "react-mosaic-component";
import type { MosaicNode } from "react-mosaic-component";
import "react-mosaic-component/react-mosaic-component.css";
import { Panel } from "../components/Panel";
import { MosaicPanel } from "./MosaicPanel";
import { insertPanel, panelIds, panelPath, validateLayouts } from "./layout";
import type { LayoutDefinition, PanelDefinition } from "./layout";

export type ViewDefinition = {
    id: string;
    title: string;
    render: (panelId: string) => ReactNode;
};

type WorkspaceProps = {
    views: ViewDefinition[];
    layouts: readonly LayoutDefinition[];
    initialLayoutId: string;
    actions?: ReactNode;
    status?: ReactNode;
    footerStatus?: ReactNode;
};
type PanelState = PanelDefinition & {
    host: HTMLDivElement;
    visited: string[];
};

function createPanel(definition: PanelDefinition, visible: boolean): PanelState {
    const host = document.createElement("div");
    host.className = "workspace-panel-host";
    return { ...definition, host, visited: visible ? [definition.viewId] : [] };
}

/** 配置はMosaic、パネル内の入力とビュー履歴は固定のPortal先で保持する。 */
export function Workspace({ views, layouts, initialLayoutId, actions, status, footerStatus }: WorkspaceProps) {
    const [state, setState] = useState(() => {
        const definitions = validateLayouts(layouts, views.map(view => view.id), initialLayoutId);
        const trees = Object.fromEntries(layouts.map(layout => [layout.id, layout.layout]));
        const visible = new Set(panelIds(trees[initialLayoutId]));
        return {
            layoutId: initialLayoutId,
            maximizedPanelId: null as string | null,
            trees,
            panels: definitions.map(panel => createPanel(panel, visible.has(panel.id))),
        };
    });
    const [parking] = useState(() => document.createElement("div"));
    const tree = state.maximizedPanelId ?? state.trees[state.layoutId];
    const visibleIds = panelIds(tree);

    function changeLayout(layoutId: string) {
        setState(current => {
            const visible = new Set(panelIds(current.trees[layoutId]));
            return {
                ...current, layoutId, maximizedPanelId: null,
                panels: current.panels.map(panel => visible.has(panel.id) && !panel.visited.length
                    ? { ...panel, visited: [panel.viewId] } : panel),
            };
        });
    }

    function changeTree(next: MosaicNode<string> | null) {
        setState(current => current.maximizedPanelId ? current : {
            ...current, trees: { ...current.trees, [current.layoutId]: next },
        });
    }

    function changeView(id: string, viewId: string) {
        setState(current => ({
            ...current,
            panels: current.panels.map(panel => panel.id === id ? {
                ...panel, viewId,
                visited: panel.visited.includes(viewId) ? panel.visited : [...panel.visited, viewId],
            } : panel),
        }));
    }

    function closePanel(id: string) {
        setState(current => {
            const original = current.trees[current.layoutId];
            const path = panelPath(original, id);
            if (path === null) return current;
            return {
                ...current, maximizedPanelId: null,
                trees: {
                    ...current.trees,
                    [current.layoutId]: path.length === 0 ? null : updateTree(original!, [createRemoveUpdate(original!, path)]),
                },
            };
        });
    }

    function splitPanel(id: string) {
        const source = state.panels.find(panel => panel.id === id)!;
        // DOMコンテナーは状態更新関数の外で一度だけ作る。
        let number = 1;
        while (state.panels.some(panel => panel.id === `P${number}`)) number++;
        const panel = createPanel({ id: `P${number}`, viewId: source.viewId, title: source.title }, true);
        setState(current => ({
            ...current,
            maximizedPanelId: null,
            panels: [...current.panels, panel],
            trees: {
                ...current.trees,
                [current.layoutId]: insertPanel(current.trees[current.layoutId], id, panel.id, "row"),
            },
        }));
    }

    function toggleMaximize(id: string) {
        setState(current => ({
            ...current, maximizedPanelId: current.maximizedPanelId === id ? null : id,
        }));
    }

    function restoreInitialLayout() {
        setState(current => {
            const original = layouts.find(layout => layout.id === current.layoutId)!.layout;
            const visible = new Set(panelIds(original));
            return {
                ...current, maximizedPanelId: null,
                trees: { ...current.trees, [current.layoutId]: original },
                panels: current.panels.map(panel => visible.has(panel.id) && !panel.visited.length
                    ? { ...panel, visited: [panel.viewId] } : panel),
            };
        });
    }

    return (
        <main className="workspace-shell">
            <header className="workspace-toolbar">
                <h1>計測ワークスペース</h1>
                <nav className="layout-buttons" aria-label="パネル配置">
                    {layouts.map(layout => (
                        <button key={layout.id} type="button" aria-pressed={state.layoutId === layout.id}
                            onClick={() => changeLayout(layout.id)}>{layout.title}</button>
                    ))}
                </nav>
                {actions}
            </header>
            {status}
            <div className="workspace-mosaic">
                <Mosaic<string>
                    value={tree}
                    onChange={changeTree}
                    className="workspace-mosaic-theme"
                    dropBehavior="split-and-swap"
                    resize={{ minimumPaneSizePercentage: 0, minimumPaneSizePx: { row: 80, column: 50 } }}
                    zeroStateView={<div className="view-message">
                        表示するパネルがありません。
                        <button type="button" onClick={restoreInitialLayout}>初期配置に戻す</button>
                    </div>}
                    renderTile={(id, path) => {
                        const panel = state.panels.find(item => item.id === id)!;
                        const view = views.find(item => item.id === panel.viewId)!;
                        return <MosaicPanel id={id} path={path} title={panel.title ?? view.title}
                            host={panel.host} parking={parking} onClose={() => closePanel(id)}
                            onSplit={() => splitPanel(id)} onToggleMaximize={() => toggleMaximize(id)}
                            maximized={state.maximizedPanelId === id}
                            selector={<select aria-label={`${id}のビュー`} value={panel.viewId}
                                onChange={event => changeView(id, event.target.value)}>
                                {views.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
                            </select>}
                        />;
                    }}
                />
            </div>
            <div hidden ref={node => { if (node) node.appendChild(parking); }} />
            {state.panels.filter(panel => panel.visited.length).map(panel => createPortal(
                <Activity mode={visibleIds.includes(panel.id) ? "visible" : "hidden"}>
                    <Panel id={panel.id} title={panel.title ?? views.find(view => view.id === panel.viewId)!.title} showHeader={false}>
                        {panel.visited.map(id => (
                            <Activity key={id} mode={id === panel.viewId ? "visible" : "hidden"}>
                                <div className="workspace-view">{views.find(view => view.id === id)!.render(panel.id)}</div>
                            </Activity>
                        ))}
                    </Panel>
                </Activity>, panel.host, panel.id,
            ))}
            <footer className={`workspace-footer${footerStatus ? " has-status" : ""}`}>
                <span className="workspace-footer-count">{visibleIds.length}パネル</span>
                {footerStatus && <div className="workspace-footer-status">{footerStatus}</div>}
            </footer>
        </main>
    );
}
