import { useState, useSyncExternalStore } from "react";
import { Badge } from "@astryxdesign/core/Badge";
import { Button } from "@astryxdesign/core/Button";
import { Card } from "@astryxdesign/core/Card";
import { Text } from "@astryxdesign/core/Text";
import { backendStore } from "../state/backendStore";
import { ViewLayout, ViewMessage } from "../components/View";
import { DeviceDetails, DeviceStatus } from "../components/DeviceInfo";
import { OperationButton } from "../components/OperationButton";
import "./devices.css";

/** 機器の探索・接続・切断を行う。選択した機器はこのパネルで保持する。 */
export function DeviceManagerView({ panelId }: { panelId: string }) {
    const backend = useSyncExternalStore(backendStore.subscribe, backendStore.getSnapshot);
    const [selectedId, setSelectedId] = useState("");
    const [error, setError] = useState("");
    const [operating, setOperating] = useState(false);
    const devices = backend.devices ?? [];
    const selected = devices.find((device) => device.id === selectedId);
    const scanning = (backend.requests["devices/scan"]?.pending ?? 0) > 0;
    const scanError = backend.requests["devices/scan"]?.error;
    const scanDisabled = scanning || backend.synchronization.status !== "ready"
        || Object.values(backend.requests).some((request) => request.outcomeUnknown || request.pending > 0 && request.writing);

    async function scan() {
        if ((backendStore.getSnapshot().requests["devices/scan"]?.pending ?? 0) > 0) return;
        setError("");
        try {
            await backendStore.scanDevices();
        } catch {
            // 探索エラーは共有ストアから表示する。
        }
    }
    async function changeConnection(deviceId: string, connect: boolean) {
        setError("");
        setOperating(true);
        try {
            if (connect) await backendStore.connectDevice(deviceId);
            else await backendStore.disconnectDevice(deviceId);
        } catch (cause) {
            setError(`${deviceId}の${connect ? "接続または接続後の状態確認" : "切断または切断後の状態確認"}に失敗しました。${cause instanceof Error ? cause.message : String(cause)}`);
            setOperating(false);
            return;
        }
        if (connect && !backendStore.getSnapshot().sampling[deviceId]) {
            try {
                await backendStore.startSampling(deviceId);
            } catch (cause) {
                setError(`${deviceId}の接続後、取得開始または開始後の状態確認に失敗しました。${cause instanceof Error ? cause.message : String(cause)}`);
            }
        }
        setOperating(false);
    }

    return <ViewLayout>
        <div className="device-manager">
            {error && <ViewMessage tone="error">{error}</ViewMessage>}
            {scanError && <ViewMessage tone="error">探索に失敗しました。{scanError}</ViewMessage>}
            <Card className="device-section device-list-section device-manager-card" variant="transparent" padding={2}
                role="region" aria-label={`${panelId}の機器一覧`}>
                <div className="device-list-heading">
                    <Text as="h3" type="label" weight="semibold">機器一覧</Text>
                    <Badge className="device-list-count" variant="neutral" label={backend.devices === null ? "—" : `${devices.length}台`} />
                    <Button className="ui-button" size="sm" variant="secondary" label={scanning ? "検索中…" : "再検索"}
                        isDisabled={scanDisabled} aria-busy={scanning} onClick={() => { void scan(); }}
                        icon={<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                            <path d="M13 5a5.5 5.5 0 1 0 .3 5M13 2v3H10" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>} />
                </div>
                {backend.devices === null ? !scanning && <ViewMessage>機器情報を確認しています…</ViewMessage>
                    : devices.length === 0 ? !scanning && !scanError && <ViewMessage>機器が見つかりません。</ViewMessage>
                    : <ul className="device-list" aria-label={`${panelId}のデバイス一覧`}>
                    {devices.map((device) => <li key={device.id} className={`device-row ${selectedId === device.id ? "selected" : ""}`}>
                        <button type="button" className="device-select"
                            aria-label={`${device.model}（${device.serial_number || device.id}）を選択`}
                            aria-pressed={selectedId === device.id} data-device-id={device.id}
                            onClick={() => setSelectedId(current => current === device.id ? "" : device.id)} />
                        <span className="device-label">
                            <Text className="device-model" type="large" weight="semibold">{device.model}</Text>
                            <Text className="device-description" type="supporting">{({ camera: "カメラ", power_meter: "パワーメーター" } as Record<string, string>)[device.type] ?? device.type}・{device.serial_number || "シリアル番号未登録"}</Text>
                        </span>
                        <div className={`device-row-actions ${device.status !== "disconnected" ? "device-action-disconnect" : "device-action-connect"}`}>
                            <OperationButton request={{ operation: device.status !== "disconnected" ? "disconnect" : "connect", deviceId: device.id }}
                                label={`${device.model}（${device.serial_number || device.id}）を${device.status !== "disconnected" ? "切断" : "接続"}`}
                                disabledReason={operating ? "操作中です。" : undefined}
                                onClick={() => { void changeConnection(device.id, device.status === "disconnected"); }}>
                                {device.status !== "disconnected" ? "切断" : "接続"}
                            </OperationButton>
                            <span className="device-states">
                                <DeviceStatus status={device.status} />
                                {device.status === "connected" && <span className={`device-sampling ${backend.synchronization.status === "ready" && backend.sampling[device.id] === true ? "device-sampling-active" : ""}`}>
                                    {backend.synchronization.status !== "ready" || backend.sampling[device.id] === undefined
                                        ? "取得状態未確認" : backend.sampling[device.id] ? "取得中" : "取得停止中"}
                                </span>}
                            </span>
                            {device.error && <Text type="supporting">{device.error}</Text>}
                        </div>
                    </li>)}
                </ul>}
            </Card>
            {selected ? <DeviceDetails device={selected} compact />
                : selectedId && devices.length > 0 && <ViewMessage tone="warning">選択した機器が見つかりません。</ViewMessage>}
        </div>
    </ViewLayout>;
}
