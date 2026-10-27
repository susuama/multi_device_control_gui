import { useState } from "react";
import type { ReactNode } from "react";
import { Panel } from "../components/Panel";

export type ViewDefinition = {
    id: string;
    title: string;
    render: (panelId: string) => ReactNode;
};

type Layout = "single" | "columns" | "rows";
type WorkspaceProps = { views: ViewDefinition[]; actions?: ReactNode };

const layouts: { id: Layout; title: string }[] = [
    { id: "single", title: "1パネル" },
    { id: "columns", title: "左右2パネル" },
    { id: "rows", title: "上下2パネル" },
];

/** パネルの配置と表示ビューを管理する。ログや機器データは所有しない。 */
export function Workspace({ views, actions }: WorkspaceProps) {
    const [layout, setLayout] = useState<Layout>("single");
    const [viewIds, setViewIds] = useState([views[0].id, views[0].id]);
    const panelCount = layout === "single" ? 1 : 2;

    function changeView(index: number, viewId: string) {
        setViewIds((current) => current.map((value, position) => position === index ? viewId : value));
    }

    return (
        <main className="workspace-shell">
            <header className="workspace-toolbar">
                <h1>計測ワークスペース</h1>
                <nav className="layout-buttons" aria-label="パネル配置">
                    {layouts.map((item) => (
                        <button key={item.id} type="button" aria-pressed={layout === item.id} onClick={() => setLayout(item.id)}>
                            {item.title}
                        </button>
                    ))}
                </nav>
                {actions}
            </header>
            <div className={`workspace-grid layout-${layout}`}>
                {viewIds.slice(0, panelCount).map((viewId, index) => {
                    const view = views.find((item) => item.id === viewId)!;
                    const panelId = `P${index + 1}`;
                    return (
                        <Panel key={panelId} id={panelId} title={view.title} viewSelector={
                            <span className="view-switch">
                                <svg width="20" height="16" viewBox="0 0 20 16" fill="none" stroke="currentColor" aria-hidden="true">
                                    <rect x="1" y="2" width="11" height="11" rx="1" /><path d="M1 5h11m3 2 2 2 2-2" />
                                </svg>
                                <select aria-label={`${panelId}のビュー`} value={viewId} onChange={(event) => changeView(index, event.target.value)}>
                                    {views.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
                                </select>
                            </span>
                        }>
                            {view.render(panelId)}
                        </Panel>
                    );
                })}
            </div>
            <footer className="workspace-footer">
                <span>{panelCount}パネル</span>
                <span>パネル左上からビューを切り替えられます</span>
            </footer>
        </main>
    );
}
