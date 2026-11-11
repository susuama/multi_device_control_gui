import { StrictMode, useEffect, useState, useSyncExternalStore } from "react";
import { createRoot } from "react-dom/client";
import { Workspace } from "../../src/workspace/Workspace";
import type { LayoutDefinition } from "../../src/workspace/layout";
import { LogView } from "../../src/views/LogView";
import { ApiTestView } from "../../src/views/ApiTestView";
import { BackendStatus, BackendStatusSummary } from "../../src/components/BackendStatus";
import { DeviceDataView } from "../../src/components/View";
import { OperationButton } from "../../src/components/OperationButton";
import { backendStore } from "../../src/state/backendStore";
import { measurementStore } from "../../src/state/measurementStore";
import { applicationRuntime } from "../../src/state/applicationRuntime";
import { preferencesStore } from "../../src/state/preferencesStore";
import { disposeWebSocketClients } from "../../src/api/websocketClient";
import { logger, logStore } from "../../src/logging/logStore";
import "../../src/index.css";
import "../../src/workspace/workspace.css";

let activeViews = 0;
let received = 0;
const unsubscribe = measurementStore.subscribe(() => { received++; });
const errors: string[] = [];
window.addEventListener("error", (event) => errors.push(event.message));
window.addEventListener("unhandledrejection", (event) => errors.push(String(event.reason)));
const reportError = (cause: unknown) => errors.push(String(cause));

/** 表示対象・機器別の未適用入力・表示範囲を持つ、検証専用のビュー。 */
export function ProbeView({ panelId }: { panelId: string }) {
    const [target, setTarget] = useState("meter-1");
    const [drafts, setDrafts] = useState<Record<string, string>>({});
    const [range, setRange] = useState("60");
    const [zoom, setZoom] = useState("1");
    const data = useSyncExternalStore(measurementStore.subscribe, measurementStore.getSnapshot);
    useEffect(() => { activeViews++; return () => { activeViews--; }; }, []);
    return <DeviceDataView deviceId={target} source="measurement" toolbar={<>
        <select aria-label={`${panelId}の対象`} value={target} onChange={(event) => setTarget(event.target.value)}>
            <option>meter-1</option><option>meter-2</option>
        </select>
        <input aria-label={`${panelId}の未適用入力`} value={drafts[target] ?? ""}
            onChange={(event) => setDrafts((current) => ({ ...current, [target]: event.target.value }))} />
        <input aria-label={`${panelId}の表示範囲`} value={range} onChange={(event) => setRange(event.target.value)} />
        <input aria-label={`${panelId}の倍率`} value={zoom} onChange={(event) => setZoom(event.target.value)} />
        <OperationButton request={{ operation: "connect", deviceId: target }}
            onClick={() => { void backendStore.connectDevice(target).catch(reportError); }}>接続</OperationButton>
        <OperationButton request={{ operation: "startSampling", deviceId: target }}
            onClick={() => { void backendStore.startSampling(target).catch(reportError); }}>取得開始</OperationButton>
    </>}>
        <output data-latest={panelId}>{JSON.stringify(data.measurements[target]?.at(-1)?.data)}</output>
        {Array.from({ length: 80 }, (_, index) => <p key={index}>スクロール領域 {index}</p>)}
    </DeviceDataView>;
}

const views = [
    { id: "log", title: "ログ", render: (panelId: string) => <LogView panelId={panelId} /> },
    { id: "api-test", title: "APIテスト", render: (panelId: string) => <ApiTestView panelId={panelId} /> },
    { id: "probe", title: "検証", render: (panelId: string) => <ProbeView panelId={panelId} /> },
];

const panels = [{ id: "P1", viewId: "log" }, { id: "P2", viewId: "log" }];
const layouts: LayoutDefinition[] = [
    { id: "single", title: "1パネル", panels: panels.slice(0, 1), layout: "P1" },
    { id: "columns", title: "左右2パネル", panels,
        layout: { type: "split", direction: "row", children: ["P1", "P2"] } },
    { id: "rows", title: "上下2パネル", panels,
        layout: { type: "split", direction: "column", children: ["P1", "P2"] } },
];

export function Harness() {
    const { theme } = useSyncExternalStore(preferencesStore.subscribe, preferencesStore.getSnapshot);
    return <div className={`application theme-${theme}`}>
        <Workspace views={views} layouts={layouts} initialLayoutId="single" status={<BackendStatus />} footerStatus={<BackendStatusSummary />} />
    </div>;
}

function require(condition: unknown, message: string): asserts condition {
    if (!condition) throw new Error(message);
}
const pause = (ms = 40) => new Promise((resolve) => setTimeout(resolve, ms));
async function waitFor(predicate: () => unknown, message: string) {
    const deadline = Date.now() + 12000;
    while (!predicate()) {
        require(Date.now() < deadline, `待機期限: ${message}`);
        await pause();
    }
}
function element<T extends HTMLElement>(selector: string): T {
    const value = document.querySelector<T>(selector);
    require(value, `要素がありません: ${selector}`);
    return value;
}
function input(label: string, value: string) {
    const node = element<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(`[aria-label="${label}"]`);
    setValue(node, value);
}
function setValue(node: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement, value: string) {
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), "value")!.set!;
    setter.call(node, value);
    node.dispatchEvent(new Event(node instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
}
async function switchView(panel: string, view: string) {
    input(`${panel}のビュー`, view);
    await pause();
}
async function layout(title: string) {
    const button = [...document.querySelectorAll<HTMLButtonElement>(".layout-buttons button")].find((node) => node.textContent === title)!;
    button.click();
    await pause();
}
async function dragSplit(selector: string, offset: number, index = 0) {
    const node = [...document.querySelectorAll<HTMLElement>(selector)].filter(item => item.getClientRects().length)[index];
    require(node, `境界がありません: ${selector} ${index}`);
    const rect = node.getBoundingClientRect();
    const horizontal = node.classList.contains("-row");
    const start = { clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 };
    const end = { clientX: start.clientX + (horizontal ? offset : 0), clientY: start.clientY + (horizontal ? 0 : offset) };
    node.dispatchEvent(new MouseEvent("mousedown", { ...start, button: 0, buttons: 1, bubbles: true }));
    document.dispatchEvent(new MouseEvent("mousemove", { ...end, buttons: 1, bubbles: true }));
    await pause(30);
    document.dispatchEvent(new MouseEvent("mouseup", { ...end, button: 0, bubbles: true }));
    await pause();
}

async function movePanel(sourceId: string, destinationId: string, position: string) {
    const source = element<HTMLSelectElement>(`[aria-label="${sourceId}のビュー"]`).closest(".workspace-panel-header")!;
    const destination = element<HTMLSelectElement>(`[aria-label="${destinationId}のビュー"]`).closest(".mosaic-window")!;
    const transfer = new DataTransfer();
    const bounds = source.getBoundingClientRect();
    source.dispatchEvent(new DragEvent("dragstart", {
        bubbles: true, cancelable: true, dataTransfer: transfer, clientX: bounds.x + 50, clientY: bounds.y + 10,
    }));
    await pause(100);
    const drop = destination.querySelector<HTMLElement>(`.drop-target.${position}`)!;
    const rect = drop.getBoundingClientRect();
    const options = { bubbles: true, cancelable: true, dataTransfer: transfer, clientX: rect.x + rect.width / 2, clientY: rect.y + rect.height / 2 };
    drop.dispatchEvent(new DragEvent("dragenter", options));
    drop.dispatchEvent(new DragEvent("dragover", options));
    await pause(50);
    drop.dispatchEvent(new DragEvent("drop", options));
    source.dispatchEvent(new DragEvent("dragend", options));
    await pause(100);
}

async function readyAction(action: () => Promise<unknown>) {
    await new Promise<void>((resolve, reject) => {
        const deadline = Date.now() + 12000;
        const check = () => {
            if (backendStore.getSnapshot().synchronization.status === "ready") {
                action().then(() => resolve(), reject);
            } else if (Date.now() > deadline) {
                reject(new Error("操作前の状態確認が完了しません。"));
            } else setTimeout(check, 20);
        };
        check();
    });
}
const control = (body: object) => fetch("/_test/control", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});
async function writes() {
    const { requests } = await (await fetch("/_test/metrics")).json();
    return JSON.stringify(Object.fromEntries(Object.entries(requests).filter(([key]) => !key.startsWith("GET "))));
}

async function checkFooterLayout(width: number) {
    const application = element<HTMLElement>(".application");
    const previousWidth = application.style.width;
    application.style.width = `${width}px`;
    await pause();
    const footer = element<HTMLElement>(".workspace-footer");
    const bounds = footer.getBoundingClientRect();
    const time = element<HTMLElement>(".workspace-footer .backend-status-time");
    require(Math.abs(bounds.height - 22) < 1, "共通フッターの高さが22pxを超えています。");
    require(getComputedStyle(time).fontSize === "11px", "時刻の文字サイズが変わりました。");
    require(footer.scrollWidth <= footer.clientWidth, `幅${width}pxで共通フッターが横にはみ出しました。`);
    const refresh = element<HTMLButtonElement>(".workspace-footer .backend-status-summary button");
    for (const node of [time, refresh]) {
        const rect = node.getBoundingClientRect();
        require(rect.top >= bounds.top && rect.bottom <= bounds.bottom && rect.left >= bounds.left && rect.right <= bounds.right,
            `幅${width}pxで時刻・再取得ボタンがフッターからはみ出しました。`);
    }
    application.style.width = previousWidth;
    await pause();
}

async function verify() {
    const checks: string[] = [];
    await applicationRuntime.start();
    await waitFor(() => document.querySelector('[aria-label="P1のログ表示件数"]'), "初回描画");
    await waitFor(() => document.querySelector(".workspace-footer time"), "フッターの最終確認時刻");
    require(document.querySelectorAll(".backend-status-summary").length === 1, "状態メッセージが重複しています。");
    require(!document.querySelector(".workspace-panel-footer"), "パネルのフッターが残っています。");
    require(!document.querySelector(".backend-status")?.textContent?.includes("最終確認"), "上部に最終確認時刻が残っています。");
    require(!document.querySelector(".backend-status .backend-status-message"), "上部に状態メッセージが残っています。");
    require(!document.querySelector(".backend-status-message"), "通常状態のメッセージが残っています。");
    require(!element<HTMLElement>(".workspace-footer").textContent?.includes("パネル左上"), "フッターに操作案内が残っています。");
    require(document.querySelectorAll(".backend-status-summary button").length === 1, "再取得ボタンが重複しています。");
    require(!document.querySelector(".backend-status button"), "上部に再取得ボタンが残っています。");
    const firstConfirmation = element<HTMLTimeElement>(".workspace-footer time").dateTime;
    await waitFor(() => element<HTMLTimeElement>(".workspace-footer time").dateTime > firstConfirmation, "周期取得後の最終確認時刻更新");
    for (const width of [640, 420, 320, 240]) await checkFooterLayout(width);
    checks.push("通常状態・操作案内の説明文を表示せず、最終確認時刻の周期更新と240〜640px幅の配置を維持");
    require(!document.querySelector('[aria-label="P2のログ表示件数"]'), "未表示の2枚目を初期化しています。");
    input("P1のログ表示件数", "10");
    element<HTMLInputElement>(".follow-tail input").click();
    await switchView("P1", "api-test");
    const details = element<HTMLDetailsElement>(".api-group:nth-child(3)");
    details.open = true;
    setValue(element<HTMLInputElement>("#P1-connect-device-id"), "入力中の機器ID");
    setValue(element<HTMLTextAreaElement>("#P1-put-settings-body"), '{"draft": 123}');
    setValue(element<HTMLInputElement>("#P1-websocket-key"), "camera");
    setValue(element<HTMLInputElement>("#P1-websocket-queue"), "1");
    await pause();
    await switchView("P1", "log");
    require(element<HTMLSelectElement>('[aria-label="P1のログ表示件数"]').value === "10", "ログ表示件数が失われました。");
    require(!element<HTMLInputElement>(".follow-tail input").checked, "ログ追従設定が失われました。");
    await switchView("P1", "api-test");
    require(details.open, "折りたたみ状態が失われました。");
    require(element<HTMLInputElement>("#P1-connect-device-id").value === "入力中の機器ID", "機器ID入力が失われました。");
    require(element<HTMLTextAreaElement>("#P1-put-settings-body").value === '{"draft": 123}', "未適用JSONが失われました。");
    require(element<HTMLInputElement>('[id="P1-websocket-key"]').value === "camera", "購読キーが失われました。");
    checks.push("ログ設定・API入力・折りたたみ状態をビュー切り替え後に復元");

    await switchView("P1", "log");
    input("P1のログ表示件数", "1000");
    for (let index = 0; index < 1000; index++) logger.info("verification", `表示負荷の回帰 ${index}`);
    await waitFor(() => element<HTMLTextAreaElement>(".log-text").value.includes("表示負荷の回帰 999"), "1000件の最終ログ反映");
    const log = element<HTMLTextAreaElement>(".log-text");
    log.scrollTop = 100;
    logger.info("verification", "循環後の末尾");
    await waitFor(() => log.value.includes("循環後の末尾"), "最後の追加後のログ反映");
    require(Math.abs(log.scrollTop - 100) < 2, "ログの追従OFFでスクロール位置が変わりました。");
    require(log.value.split("\n").length === 1000 && log.value.includes("循環後の末尾"), "ログの上限循環で表示内容が欠落しました。");
    await switchView("P1", "api-test");
    logger.info("verification", "非表示中の追加");
    await switchView("P1", "log");
    require(log.value.includes("非表示中の追加"), "ログの再表示で最終状態を反映しませんでした。");
    element<HTMLInputElement>(".follow-tail input").click(); await pause();
    require(log.scrollHeight - log.clientHeight - log.scrollTop < 2, "ログの追従ONで末尾へ移動しませんでした。");
    input("P1のログ表示件数", "50"); await pause();
    require(log.value.split("\n").length === 50, "ログの表示件数を変更できません。");
    logStore.clear(); await pause();
    require(!log.value.includes("表示負荷の回帰") && !log.value.includes("非表示中の追加"), "クリア後に古い整形済みログが残っています。");
    await switchView("P1", "api-test");
    checks.push("ログ1000件の循環・追従ON/OFF・非表示からの最終状態反映・50件表示・クリア");

    await layout("左右2パネル");
    const firstPanel = element<HTMLElement>(".workspace-panel");
    const initialWidth = firstPanel.getBoundingClientRect().width;
    await dragSplit(".mosaic-split.-row", 25);
    await pause();
    const resizedWidth = firstPanel.getBoundingClientRect().width;
    require(resizedWidth > initialWidth, "左右の境界操作で幅が変わりません。");
    input("P2のログ表示件数", "50");
    await switchView("P2", "api-test");
    setValue(element<HTMLInputElement>("#P2-connect-device-id"), "2枚目の入力");
    await pause();
    await layout("1パネル");
    require(element<HTMLElement>('[aria-label="APIテスト P2"]').getClientRects().length === 0, "2枚目が表示されたままです。");
    await layout("上下2パネル");
    const initialHeight = firstPanel.getBoundingClientRect().height;
    await dragSplit(".mosaic-split.-column", 25);
    await pause();
    require(firstPanel.getBoundingClientRect().height > initialHeight, "上下の境界操作で高さが変わりません。");
    await layout("左右2パネル");
    require(Math.abs(firstPanel.getBoundingClientRect().width - resizedWidth) < 1, "左右の分割比率が失われました。");
    await layout("上下2パネル");
    require(element<HTMLInputElement>("#P2-connect-device-id").value === "2枚目の入力", "2枚目の入力が失われました。");
    require(element<HTMLInputElement>("#P1-connect-device-id").value === "入力中の機器ID", "入力がパネル間で混ざりました。");
    checks.push("2枚目の非表示・再表示とパネル間の入力独立");

    await switchView("P1", "probe");
    await switchView("P2", "probe");
    const panel = element<HTMLElement>('[aria-label="検証 P1"]');
    const startButton = [...panel.querySelectorAll<HTMLButtonElement>("button")].find((node) => node.textContent === "取得開始")!;
    require(startButton.disabled, "未接続で取得開始が有効です。");
    require(startButton.getAttribute("aria-description")?.includes("接続していません"), "操作不可の理由が支援技術へ渡されていません。");
    require(!panel.querySelector(".operation-reason"), "操作不可の理由文をボタン横へ表示しています。");
    input("P1の未適用入力", "meter-1の入力");
    input("P1の表示範囲", "120");
    input("P1の倍率", "2");
    input("P1の対象", "meter-2");
    await pause();
    input("P1の未適用入力", "meter-2の入力");
    input("P1の対象", "meter-1");
    await pause();
    input("P2の未適用入力", "別パネルの入力");
    await readyAction(() => backendStore.connectDevice("meter-1"));
    await waitFor(() => !startButton.disabled, "接続後の操作可否反映");
    startButton.click();
    await waitFor(() => measurementStore.getSnapshot().measurements["meter-1"]?.length, "数値受信");
    await readyAction(() => backendStore.startRecording({ directory: "mock-recordings" }));
    const recordingId = backendStore.getSnapshot().recording!.recording_id;
    const beforeWrites = await writes();
    const beforeReceived = received;
    await switchView("P1", "log");
    await layout("1パネル");
    await waitFor(() => activeViews === 0, "非表示ビューのEffect解除");
    await pause(350);
    require(received > beforeReceived, "ビューを隠すと共通受信が止まりました。");
    require(await writes() === beforeWrites, "ビュー切り替えで変更要求を送信しました。");
    require(backendStore.getSnapshot().recording!.recording_id === recordingId && backendStore.getSnapshot().recording!.is_recording, "記録が停止しました。");
    await layout("左右2パネル");
    await switchView("P1", "probe");
    await waitFor(() => activeViews === 2, "ビューの購読再開");
    require(element<HTMLInputElement>('[aria-label="P1の未適用入力"]').value === "meter-1の入力", "機器別の未適用入力が失われました。");
    require(element<HTMLInputElement>('[aria-label="P1の表示範囲"]').value === "120", "表示範囲が失われました。");
    require(element<HTMLInputElement>('[aria-label="P1の倍率"]').value === "2", "倍率が失われました。");
    require(element<HTMLInputElement>('[aria-label="P2の未適用入力"]').value === "別パネルの入力", "未適用入力がパネル間で混ざりました。");
    require(element<HTMLOutputElement>('[data-latest="P1"]').textContent === element<HTMLOutputElement>('[data-latest="P2"]').textContent, "共有データが一致しません。");
    checks.push("機器別入力・表示範囲・倍率の復元、非表示Effect解除、共有取得・記録の継続、操作可否の追従");

    await control({ offline: true });
    await applicationRuntime.refresh().catch(() => {});
    await pause();
    require(panel.querySelector(".view-message-error"), "通信異常が表示されません。");
    require(element<HTMLOutputElement>('[data-latest="P1"]').textContent?.includes("power_w"), "通信断で最後の値を消しました。");
    require(document.querySelector(".backend-status [role=alert]"), "全体の通信異常が表示されません。");
    require(Date.parse(element<HTMLTimeElement>(".workspace-footer time").dateTime) === backendStore.getSnapshot().synchronization.lastUpdatedAt,
        "通信異常で最後に確認した時刻が失われました。");
    await checkFooterLayout(320);
    await control({ offline: false });
    await waitFor(() => !element<HTMLButtonElement>(".workspace-footer .backend-status-summary button").disabled, "再取得ボタンの操作可否");
    element<HTMLButtonElement>(".workspace-footer .backend-status-summary button").click();
    await waitFor(() => backendStore.getSnapshot().synchronization.status === "ready", "再取得による復旧");
    checks.push("通信異常と最終受信時刻の表示、最後のデータ保持、再取得による復旧");

    await layout("上下2パネル");
    await dragSplit(".mosaic-split.-column", -10000);
    try {
        await waitFor(() => getComputedStyle(panel.querySelector(".workspace-panel-body")!).display === "none", "最小高さで本文を非表示");
    } catch (cause) {
        throw new Error(`${cause} height=${panel.getBoundingClientRect().height} min=${getComputedStyle(panel).minHeight} visibility=${document.visibilityState}`, { cause });
    }
    require(!panel.querySelector("footer"), "パネルのフッターが残っています。");
    require(Math.abs(panel.closest(".mosaic-window")!.getBoundingClientRect().height - 50) < 1, "パネルの最小高さが50pxではありません。");
    await dragSplit(".mosaic-split.-column", 25);
    await waitFor(() => getComputedStyle(panel.querySelector(".workspace-panel-body")!).display !== "none", "再拡大");
    require(element<HTMLInputElement>('[aria-label="P1の未適用入力"]').value === "meter-1の入力", "最小化で入力が失われました。");
    checks.push("左右・上下の境界操作、配置ごとの比率保持、最小高さ50pxからの再拡大と入力保持");
    preferencesStore.update({ theme: "light" });
    await pause();
    require(getComputedStyle(panel.closest(".mosaic-window")!).backgroundColor === "rgb(243, 243, 243)", "ライト配色が反映されません。");
    for (const width of [640, 420, 320, 240]) await checkFooterLayout(width);
    document.querySelector<HTMLElement>(".application")!.style.width = "420px";
    await layout("1パネル");
    require(document.documentElement.scrollWidth <= window.innerWidth, "狭い幅で画面全体が横にはみ出しました。");
    checks.push("最小高さ・長い本文・狭い幅・ライト配色でもフッターと入力を維持");
    await layout("左右2パネル");
    await switchView("P1", "probe"); await switchView("P2", "probe");
    const preserved = element<HTMLElement>('[aria-label="検証 P1"]');
    const originalLeft = preserved.getBoundingClientRect().left;
    await movePanel("P1", "P2", "swap");
    require(preserved.getBoundingClientRect().left > originalLeft + 50, "中央へのドロップで配置が入れ替わりません。");
    require(element<HTMLElement>('[aria-label="検証 P1"]') === preserved, "配置変更でパネルDOMを作り直しました。");
    require(element<HTMLInputElement>('[aria-label="P1の未適用入力"]').value === "meter-1の入力", "配置変更で入力が消えました。");
    await movePanel("P1", "P2", "bottom");
    const first = preserved.getBoundingClientRect();
    const second = element<HTMLElement>('[aria-label="検証 P2"]').getBoundingClientRect();
    require(first.top > second.top + 50 && Math.abs(first.left - second.left) < 2, "辺へのドロップで上下配置に変わりません。");
    await layout("1パネル"); await layout("左右2パネル");
    require(Math.abs(preserved.getBoundingClientRect().top - first.top) < 2, "配置切替で並べ替え結果が消えました。");
    checks.push("HTML5ドラッグで中央の入れ替え・辺への再配置、DOMと入力・配置の保持");

    const mosaicArea = element<HTMLElement>(".workspace-mosaic");
    mosaicArea.style.width = "90px"; await pause();
    for (const header of document.querySelectorAll<HTMLElement>(".workspace-panel-header")) {
        const rect = header.getBoundingClientRect();
        for (const button of header.querySelectorAll("button")) {
            const bounds = button.getBoundingClientRect();
            require(bounds.left >= rect.left && bounds.right <= rect.right, "狭いパネルで操作ボタンがはみ出しました。");
        }
    }
    mosaicArea.style.width = ""; await pause();
    const writesBeforePanels = await writes();
    require(!document.querySelector(".workspace-add-panel") && !document.querySelector('[aria-label="追加するビュー"]'), "上部のパネル追加欄が残っています。");
    const beforeMaximize = preserved.getBoundingClientRect();
    const otherPanel = element<HTMLElement>('[aria-label="検証 P2"]');
    const beforeOther = otherPanel.getBoundingClientRect();
    element<HTMLButtonElement>('[aria-label="P1の最大化を切り替え"]').click(); await pause();
    require(document.querySelectorAll('.mosaic-window').length === 1, "最大化中に他のパネルを表示しています。");
    require(otherPanel.getClientRects().length === 0 && preserved.getBoundingClientRect().height > beforeMaximize.height + 50, "パネルを最大化できません。");
    await waitFor(() => activeViews === 1, "最大化で非表示になったビューの購読解除");
    require(element<HTMLElement>('[aria-label="検証 P1"]') === preserved, "最大化でパネルDOMを作り直しました。");
    element<HTMLButtonElement>('[aria-label="P1の最大化を切り替え"]').click(); await pause();
    const restored = preserved.getBoundingClientRect();
    require(Math.abs(restored.left - beforeMaximize.left) < 1 && Math.abs(restored.top - beforeMaximize.top) < 1
        && Math.abs(restored.width - beforeMaximize.width) < 1 && Math.abs(restored.height - beforeMaximize.height) < 1, "最大化解除で元の配置・比率に戻りません。");
    require(element<HTMLInputElement>('[aria-label="P1の未適用入力"]').value === "meter-1の入力", "最大化で入力が消えました。");
    checks.push("最大化と再押下での復元、DOM・入力・分割率の保持、他パネルの非表示と購読解除");

    element<HTMLButtonElement>('[aria-label="P1の最大化を切り替え"]').click(); await pause();
    element<HTMLButtonElement>('[aria-label="P1を右に分割"]').click(); await pause();
    require(element<HTMLSelectElement>('[aria-label="P3のビュー"]').value === "probe", "元と同じビューで分割していません。");
    const divided = preserved.getBoundingClientRect();
    const added = element<HTMLElement>('[aria-label="検証 P3"]').getBoundingClientRect();
    require(added.left > divided.left && Math.abs(added.top - divided.top) < 1
        && Math.abs(added.width - divided.width) < 1 && Math.abs(added.right - beforeMaximize.right) < 1
        && Math.abs(divided.left - beforeMaximize.left) < 1 && Math.abs(divided.height - beforeMaximize.height) < 1, "元の領域を右へ等分していません。");
    const afterOther = otherPanel.getBoundingClientRect();
    require(Math.abs(afterOther.left - beforeOther.left) < 1 && Math.abs(afterOther.top - beforeOther.top) < 1
        && Math.abs(afterOther.width - beforeOther.width) < 1 && Math.abs(afterOther.height - beforeOther.height) < 1, "分割で周囲の領域が変わりました。");
    require(element<HTMLInputElement>('[aria-label="P3の未適用入力"]').value === "", "新規パネルへ未適用入力を複製しました。");
    input("P3の未適用入力", "分割パネルの入力");
    await switchView("P3", "log");
    element<HTMLButtonElement>('[aria-label="P3を右に分割"]').click(); await pause();
    require(element<HTMLSelectElement>('[aria-label="P4のビュー"]').value === "log", "切り替え後の現在のビューを使わず分割しました。");
    element<HTMLButtonElement>('[aria-label="P4を閉じる"]').click(); await pause();
    await layout("1パネル"); await layout("左右2パネル"); await switchView("P3", "probe");
    require(element<HTMLInputElement>('[aria-label="P3の未適用入力"]').value === "分割パネルの入力", "分割後の入力が消えました。");
    checks.push("最大化中の分割は元の領域を右へ等分し、新ID・同じ現在ビューで作成、周囲の領域と入力を保持");

    element<HTMLButtonElement>('[aria-label="P3の最大化を切り替え"]').click(); await pause();
    element<HTMLButtonElement>('[aria-label="P3を閉じる"]').click(); await pause();
    require(document.querySelectorAll('.mosaic-window').length === 2 && element<HTMLElement>('[aria-label="検証 P3"]').getClientRects().length === 0, "最大化中の閉じるで元の配置を壊しました。");
    element<HTMLButtonElement>('[aria-label="P1の最大化を切り替え"]').click(); await pause();
    await layout("1パネル");
    require(element<HTMLButtonElement>('[aria-label="P1の最大化を切り替え"]').getAttribute("aria-pressed") === "false", "配置切替で最大化を解除していません。");
    await layout("左右2パネル");
    for (const id of ["P1", "P2"]) { element<HTMLButtonElement>(`[aria-label="${id}を閉じる"]`).click(); await pause(); }
    require(document.querySelector(".workspace-footer-count")?.textContent === "0パネル", "全パネルを閉じられません。");
    [...document.querySelectorAll<HTMLButtonElement>("button")].find(node => node.textContent === "初期配置に戻す")!.click(); await pause();
    require(document.querySelectorAll('.mosaic-window').length === 2, "空の配置から初期配置を復元できません。");
    require(await writes() === writesBeforePanels && backendStore.getSnapshot().recording!.is_recording, "配置操作で機器操作・記録停止を送信しました。");
    await layout("1パネル");
    require(element<HTMLInputElement>('[aria-label="P1の未適用入力"]').value === "meter-1の入力", "他の配置のパネル状態が変わりました。");
    checks.push("最大化中の閉じる・配置切替、空配置からの復元、取得・記録の継続と変更要求なし");
    await readyAction(() => backendStore.stopRecording());
    await readyAction(() => backendStore.stopSampling("meter-1"));
    await readyAction(() => backendStore.disconnectDevice("meter-1"));
    require(errors.length === 0, errors.join("\n"));
    return checks;
}

createRoot(document.getElementById("root")!).render(<StrictMode><Harness /></StrictMode>);
void verify().then((checks) => ({ ok: true, checks }), (cause) => ({ ok: false, error: String(cause), stack: cause?.stack }))
    .then(async (result) => {
        applicationRuntime.dispose();
        disposeWebSocketClients();
        unsubscribe();
        await fetch("/_test/result", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...result, errors }) });
    });
