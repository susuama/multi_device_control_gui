import type { DeviceInfo } from "../type/devices";
import { Card } from "@astryxdesign/core/Card";
import { Text } from "@astryxdesign/core/Text";

export function DeviceStatus({ status }: { status: DeviceInfo["status"] }) {
    return <span className={`device-status device-status-${status}`}>
        {{ connected: "接続済み", disconnected: "未接続", error: "エラー" }[status]}
    </span>;
}

export function DeviceSelector({ panelId, devices, value, onChange, disabled }: {
    panelId: string; devices: readonly DeviceInfo[]; value: string; onChange: (id: string) => void; disabled?: boolean;
}) {
    return <label className="device-selector">対象
        <select aria-label={`${panelId}の対象機器`} value={value} disabled={disabled}
            onChange={(event) => onChange(event.target.value)}>
            <option value="">機器を選択</option>
            {value && !devices.some((device) => device.id === value) && <option value={value}>一覧にない機器: {value}</option>}
            {devices.map((device) => <option key={device.id} value={device.id}>
                {device.model} / {device.serial_number || device.id}
            </option>)}
        </select>
    </label>;
}

export function DeviceDetails({ device, compact = false }: { device: DeviceInfo; compact?: boolean }) {
    const content = <>
        {compact ? <Text as="h3" type="label" weight="semibold">機器情報</Text> : <h3>機器情報</h3>}
        {compact && <div className="device-details-heading">
            <Text className="device-model" type="large" weight="semibold">{device.model}</Text>
            <DeviceStatus status={device.status} />
        </div>}
        <dl className="device-details-list">
            <dt>種類</dt><dd>{{ camera: "カメラ", power_meter: "パワーメーター" }[device.type] ?? device.type}</dd>
            <dt>メーカー</dt><dd>{device.manufacturer || "未登録"}</dd>
            {!compact && <><dt>モデル</dt><dd>{device.model}</dd></>}
            <dt>シリアル番号</dt><dd>{device.serial_number || "未登録"}</dd>
            <dt>アドレス</dt><dd>{device.address}</dd>
            <dt>機器ID</dt><dd>{device.id}</dd>
            {device.error && <><dt>エラー</dt><dd>{device.error}</dd></>}
            {!compact && <><dt>接続状態</dt><dd><DeviceStatus status={device.status} /></dd></>}
        </dl>
    </>;
    return compact ? (
        <Card className="device-section device-manager-card" variant="transparent" padding={2} role="region" aria-label="機器情報">
            {content}
        </Card>
    ) : <section className="device-section">{content}</section>;
}
