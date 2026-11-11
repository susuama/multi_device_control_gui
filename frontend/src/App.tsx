import { lazy, Suspense, useSyncExternalStore } from "react";
import { preferencesStore } from "./state/preferencesStore";
import { AppTheme } from "./theme/AppTheme";
import { Workspace } from "./workspace/Workspace";
import type { ViewDefinition } from "./workspace/Workspace";
import { layouts } from "./workspace/layouts";
import { LogView } from "./views/LogView";
import { ApiTestView } from "./views/ApiTestView";
import { BackendStatus, BackendStatusSummary } from "./components/BackendStatus";
import { DeviceManagerView } from "./views/DeviceManagerView";
import { DeviceSettingsView } from "./views/DeviceSettingsView";
import { MeasurementRecordingView } from "./views/MeasurementRecordingView";
import { MeasurementTableView } from "./views/MeasurementTableView";
import { ImageView } from "./views/ImageView";
import "./workspace/workspace.css";

const MeasurementGraphView = lazy(() => import("./views/MeasurementGraphView").then((module) => ({ default: module.MeasurementGraphView })));

// ビューを追加するときは、この一覧へ表示名と表示部品を登録する。
const views: ViewDefinition[] = [
    {
        id: "log", title: "ログ",
        render: (panelId) => <LogView panelId={panelId} />,
    },
    {
        id: "api-test", title: "APIテスト",
        render: (panelId) => <ApiTestView panelId={panelId} />,
    },
    {
        id: "devices", title: "デバイス管理",
        render: (panelId) => <DeviceManagerView panelId={panelId} />,
    },
    {
        id: "device", title: "個別デバイス",
        render: (panelId) => <DeviceSettingsView panelId={panelId} />,
    },
    {
        id: "camera-settings", title: "カメラ設定",
        render: (panelId) => <DeviceSettingsView panelId={panelId} deviceType="camera" />,
    },
    {
        id: "power-settings", title: "パワー設定",
        render: (panelId) => <DeviceSettingsView panelId={panelId} deviceType="power_meter" />,
    },
    {
        id: "measurement-recording", title: "測定・記録",
        render: (panelId) => <MeasurementRecordingView panelId={panelId} />,
    },
    {
        id: "table", title: "データのテーブル",
        render: (panelId) => <MeasurementTableView panelId={panelId} />,
    },
    {
        id: "image", title: "イメージ",
        render: (panelId) => <ImageView panelId={panelId} />,
    },
    {
        id: "graph", title: "グラフ",
        render: (panelId) => (
            <Suspense fallback={<span role="status">読み込み中</span>}>
                <MeasurementGraphView panelId={panelId} />
            </Suspense>
        ),
    },
];

function App() {
    const { theme } = useSyncExternalStore(preferencesStore.subscribe, preferencesStore.getSnapshot);
    return (
        <AppTheme mode={theme}>
            <div className={`application theme-${theme}`}>
                <Workspace
                    views={views}
                    layouts={layouts}
                    initialLayoutId="grid"
                    status={<BackendStatus />}
                    footerStatus={<BackendStatusSummary />}
                    actions={
                        <label className="theme-control">
                            配色
                            <select
                                aria-label="配色"
                                value={theme}
                                onChange={(event) => preferencesStore.update({ theme: event.target.value as "dark" | "light" })}
                            >
                                <option value="dark">ダーク</option>
                                <option value="light">ライト</option>
                            </select>
                        </label>
                    }
                />
            </div>
        </AppTheme>
    );
}

export default App;
