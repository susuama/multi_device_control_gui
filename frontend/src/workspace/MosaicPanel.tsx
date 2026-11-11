import { useLayoutEffect, useRef } from "react";
import type { ReactNode } from "react";
import { MosaicWindow } from "react-mosaic-component";
import type { MosaicPath } from "react-mosaic-component";

type Props = {
    id: string;
    title: string;
    path: MosaicPath;
    host: HTMLDivElement;
    parking: HTMLDivElement;
    selector: ReactNode;
    onClose: () => void;
    onSplit: () => void;
    onToggleMaximize: () => void;
    maximized: boolean;
};

/** 内容のPortal先を維持したまま、Mosaicの表示先へコンテナーを移す。 */
export function MosaicPanel({ id, title, path, host, parking, selector, onClose, onSplit, onToggleMaximize, maximized }: Props) {
    const target = useRef<HTMLDivElement>(null);
    useLayoutEffect(() => {
        target.current!.appendChild(host);
        return () => { parking.appendChild(host); };
    }, [host, parking]);

    return (
        <MosaicWindow<string>
            path={path}
            title={title}
            toolbarControls={[]}
            draggable={!maximized}
            renderToolbar={() => (
                <div className="workspace-panel-header">
                    <span className="view-switch" draggable={false} onDragStart={event => event.stopPropagation()}>
                        <svg width="20" height="16" viewBox="0 0 20 16" fill="none" stroke="currentColor" aria-hidden="true">
                            <rect x="1" y="2" width="11" height="11" rx="1" />
                            <path d="M1 5h11m3 2 2 2 2-2" />
                        </svg>
                        {selector}
                    </span>
                    <span className="workspace-panel-title">{title}</span>
                    <span className="panel-id">{id}</span>
                    <button type="button" aria-label={`${id}を右に分割`} title="右に分割" draggable={false}
                        onDragStart={event => event.stopPropagation()} onClick={onSplit}>
                        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" aria-hidden="true">
                            <rect x="2" y="2" width="12" height="12" rx="1" />
                            <path d="M8 2v12" />
                        </svg>
                    </button>
                    <button type="button" aria-label={`${id}の最大化を切り替え`} aria-pressed={maximized}
                        title={maximized ? "元の配置に戻す" : "最大化"} draggable={false}
                        onDragStart={event => event.stopPropagation()} onClick={onToggleMaximize}>
                        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" aria-hidden="true">
                            {maximized ? <path d="M5 5V2h9v9h-3M2 5h9v9H2z" /> : <rect x="2" y="2" width="12" height="12" rx="1" />}
                        </svg>
                    </button>
                    <button type="button" aria-label={`${id}を閉じる`} title="閉じる" draggable={false}
                        onDragStart={event => event.stopPropagation()} onClick={onClose}>×</button>
                </div>
            )}
        >
            <div ref={target} className="workspace-panel-mount" />
        </MosaicWindow>
    );
}
