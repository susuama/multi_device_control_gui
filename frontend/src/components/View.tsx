import { useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { backendStore } from "../state/backendStore";
import { measurementStore } from "../state/measurementStore";
import { getDeviceViewStatus } from "../views/viewStatus";

/** 本文をスクロールし、ツールバーと操作欄を表示領域に残す。 */
export function ViewLayout({ toolbar, actions, children }: { toolbar?: ReactNode; actions?: ReactNode; children: ReactNode }) {
    return <div className="view-layout">
        {toolbar && <div className="view-toolbar">{toolbar}</div>}
        <div className="view-content">{children}</div>
        {actions && <div className="view-actions">{actions}</div>}
    </div>;
}

export function ViewMessage({ tone = "info", children }: { tone?: "info" | "warning" | "error"; children: ReactNode }) {
    return <div className={`view-message view-message-${tone}`} role={tone === "error" ? "alert" : "status"}>{children}</div>;
}

/** 空表示や通信状態を添え、更新停止・切断時も受信済みの内容を残す。 */
export function DeviceDataView({ deviceId, source, toolbar, children }: {
    deviceId: string | null; source: "measurement" | "preview"; toolbar?: ReactNode; children: ReactNode;
}) {
    const backend = useSyncExternalStore(backendStore.subscribe, backendStore.getSnapshot);
    const data = useSyncExternalStore(measurementStore.subscribe, measurementStore.getSnapshot);
    const status = getDeviceViewStatus(backend, data, deviceId, source);
    return <ViewLayout toolbar={toolbar}>
        {status && <ViewMessage tone={status.tone}>
            {status.message}
            {status.lastReceivedAt != null && <span className="view-last-received">
                最終受信: {new Date(status.lastReceivedAt).toLocaleTimeString("ja-JP", { hour12: false })}
            </span>}
        </ViewMessage>}
        {!status?.blocking && children}
    </ViewLayout>;
}
