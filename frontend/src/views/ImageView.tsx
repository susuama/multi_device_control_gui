import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { backendStore } from "../state/backendStore";
import { measurementStore } from "../state/measurementStore";
import { DeviceSelector } from "../components/DeviceInfo";
import { DeviceDataView, ViewMessage } from "../components/View";
import type { CameraPreviewEvent } from "../type/event";
import "./image.css";

const zoomOptions = ["fit", "0.25", "0.5", "1", "2", "4"] as const;
type Zoom = typeof zoomOptions[number];

function PreviewImage({ preview, zoom, panelId }: { preview: CameraPreviewEvent; zoom: Zoom; panelId: string }) {
    const image = useRef<HTMLImageElement>(null);
    const [failed, setFailed] = useState<Blob | null>(null);
    useEffect(() => {
        const element = image.current!;
        const url = URL.createObjectURL(preview.jpeg);
        const loaded = () => setFailed(null);
        const error = () => setFailed(preview.jpeg);
        element.addEventListener("load", loaded);
        element.addEventListener("error", error);
        element.src = url;
        return () => {
            element.removeEventListener("load", loaded);
            element.removeEventListener("error", error);
            URL.revokeObjectURL(url);
        };
    }, [preview.jpeg]);
    useEffect(() => {
        const element = image.current!;
        return () => { element.removeAttribute("src"); };
    }, []);
    return <>
        {failed === preview.jpeg && <ViewMessage tone="error">プレビュー画像を読み込めません。</ViewMessage>}
        <img ref={image} alt={`${panelId}のカメラプレビュー`} hidden={failed === preview.jpeg}
            className={zoom === "fit" ? "image-preview-fit" : "image-preview-zoom"}
            style={zoom === "fit" ? undefined : { width: preview.width * Number(zoom), height: preview.height * Number(zoom) }} />
    </>;
}

/** 最新画像だけを表示し、表示倍率を機器・パネルごとに保持する。 */
export function ImageView({ panelId }: { panelId: string }) {
    const backend = useSyncExternalStore(backendStore.subscribe, backendStore.getSnapshot);
    const [deviceId, setDeviceId] = useState("");
    const [zooms, setZooms] = useState<Record<string, Zoom>>({});
    const zoom = zooms[deviceId] ?? "fit";
    const preview = useSyncExternalStore(measurementStore.subscribe, () => measurementStore.getSnapshot().previews[deviceId]);
    const stream = useSyncExternalStore(measurementStore.subscribe, () => measurementStore.getSnapshot().streams[deviceId]);
    return <DeviceDataView deviceId={deviceId || null} source="preview" toolbar={<>
        <DeviceSelector panelId={panelId} devices={(backend.devices ?? []).filter(device => device.type === "camera")}
            value={deviceId} onChange={setDeviceId} />
        <label>倍率 <select aria-label={`${panelId}の画像倍率`} value={zoom}
            onChange={event => setZooms(current => ({ ...current, [deviceId]: event.target.value as Zoom }))}>
            {zoomOptions.map(value => <option key={value} value={value}>{value === "fit" ? "パネルに収める" : `${Number(value) * 100}%`}</option>)}
        </select></label>
    </>}>
        {preview && <div className="image-display">
            <div className="image-preview-info"><span>プレビュー {preview.width} × {preview.height} px</span>
                {stream?.lastReceivedAt != null && <time dateTime={new Date(stream.lastReceivedAt).toISOString()}>最終受信: {new Date(stream.lastReceivedAt).toLocaleTimeString("ja-JP", { hour12: false })}</time>}
            </div>
            <div className="image-preview-viewport" aria-label={`${panelId}の画像表示領域`}>
                <PreviewImage key={deviceId} preview={preview} zoom={zoom} panelId={panelId} />
            </div>
        </div>}
    </DeviceDataView>;
}
