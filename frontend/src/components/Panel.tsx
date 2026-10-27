import type { ReactNode } from "react";

type PanelProps = {
    id: string;
    title: string;
    viewSelector: ReactNode;
    children: ReactNode;
};

/** ビューの内容や通信処理を持たず、共通の枠と表示領域を提供する。 */
export function Panel({ id, title, viewSelector, children }: PanelProps) {
    return (
        <section className="workspace-panel" aria-label={`${title} ${id}`}>
            <header className="workspace-panel-header">
                {viewSelector}
                <span className="workspace-panel-title">{title}</span>
                <span className="panel-id">{id}</span>
            </header>
            <div className="workspace-panel-body">{children}</div>
            <footer className="workspace-panel-footer">
                <span>{title}</span><span className="panel-id">{id}</span>
            </footer>
        </section>
    );
}
