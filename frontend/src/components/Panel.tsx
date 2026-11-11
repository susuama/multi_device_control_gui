import { Activity, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";

type PanelProps = {
    id: string;
    title: string;
    viewSelector?: ReactNode;
    showHeader?: boolean;
    children: ReactNode;
    style?: CSSProperties;
};

/** ビューの内容や通信処理を持たず、共通の枠と表示領域を提供する。 */
export function Panel({ id, title, viewSelector, children, style, showHeader = true }: PanelProps) {
    const panel = useRef<HTMLElement>(null);
    const [collapsed, setCollapsed] = useState(false);

    useLayoutEffect(() => {
        const element = panel.current!;
        const observer = new ResizeObserver(() => {
            setCollapsed(element.getBoundingClientRect().height <= (showHeader ? parseFloat(getComputedStyle(element).minHeight) : 24));
        });
        observer.observe(element);
        return () => observer.disconnect();
    }, [showHeader]);

    return (
        <section ref={panel} className="workspace-panel" aria-label={`${title} ${id}`} style={style}>
            {showHeader && <header className="workspace-panel-header">
                {viewSelector}
                <span className="workspace-panel-title">{title}</span>
                <span className="panel-id">{id}</span>
            </header>}
            <Activity mode={collapsed ? "hidden" : "visible"}>
                <div className="workspace-panel-body">{children}</div>
            </Activity>
        </section>
    );
}
