import { chartLines } from "../chartInspection";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "../../src/App";
import { backendStore } from "../../src/state/backendStore";
import { measurementStore } from "../../src/state/measurementStore";
import { applicationRuntime } from "../../src/state/applicationRuntime";
import { preferencesStore } from "../../src/state/preferencesStore";
import { disposeWebSocketClients } from "../../src/api/websocketClient";
import type { CameraSetting, PowerMeterSetting } from "../../src/type/devices";
import "../../src/index.css";

const errors: string[] = [];
const previewUrls = new Set<string>();
let previewUrlsCreated = 0;
let previewUrlsPeak = 0;
const createObjectURL = URL.createObjectURL.bind(URL);
const revokeObjectURL = URL.revokeObjectURL.bind(URL);
URL.createObjectURL = (blob) => {
    const url = createObjectURL(blob);
    if (blob instanceof Blob && blob.type === "image/jpeg") {
        previewUrls.add(url); previewUrlsCreated++; previewUrlsPeak = Math.max(previewUrlsPeak, previewUrls.size);
    }
    return url;
};
URL.revokeObjectURL = (url) => { previewUrls.delete(url); revokeObjectURL(url); };
window.addEventListener("error", (event) => errors.push(event.message));
window.addEventListener("unhandledrejection", (event) => errors.push(String(event.reason)));
function require(condition: unknown, message: string): asserts condition {
    if (!condition) throw new Error(message);
}
const pause = (ms = 40) => new Promise((resolve) => setTimeout(resolve, ms));
async function waitFor(predicate: () => unknown, message: string) {
    const deadline = Date.now() + 15000;
    while (!predicate()) {
        require(Date.now() < deadline, `待機期限: ${message} / ${JSON.stringify(backendStore.getSnapshot().requests)} / ${[...document.querySelectorAll(".device-section h3")].map(node => node.textContent).join(" / ")}`);
        await pause();
    }
}
function element<T extends HTMLElement>(selector: string): T {
    const node = [...document.querySelectorAll<T>(selector)].find(item => item.getClientRects().length > 0);
    require(node, `要素がありません: ${selector}`);
    return node;
}
function setValue(selector: string, value: string) {
    const node = element<HTMLInputElement | HTMLSelectElement>(selector);
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), "value")!.set!.call(node, value);
    node.dispatchEvent(new Event(node instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
}
async function view(panel: string, id: string) {
    setValue(`[aria-label="${panel}のビュー"]`, id);
    await pause();
}
async function target(panel: string, id: string) {
    await waitFor(() => !element<HTMLSelectElement>(`[aria-label="${panel}の対象機器"]`).disabled, "対象選択の操作可否");
    setValue(`[aria-label="${panel}の対象機器"]`, id);
    await pause();
}
function panelElement(panel: string) {
    return [...document.querySelectorAll<HTMLElement>(".workspace-panel")].find((node) => node.getAttribute("aria-label")?.endsWith(` ${panel}`))!;
}
function button(panel: string, text: string) {
    const node = [...panelElement(panel).querySelectorAll<HTMLButtonElement>("button")]
        .find((item) => item.textContent === text && item.getClientRects().length > 0);
    require(node, `ボタンがありません: ${panel} ${text}`);
    return node;
}
async function click(panel: string, text: string) {
    await waitFor(() => {
        const node = button(panel, text);
        if (node.disabled) return false;
        node.click();
        return true;
    }, `${text}の操作可否`);
    await pause();
    if (text === "設定を適用") {
        await waitFor(() => !button(panel, "設定を取得").disabled, "設定処理の完了");
    }
}
async function layout(text: string) {
    const node = [...document.querySelectorAll<HTMLButtonElement>(".layout-buttons button")].find((item) => item.textContent === text)!;
    node.click();
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

async function readyAction(action: () => Promise<unknown>) {
    let promise: Promise<unknown> | undefined;
    await waitFor(() => {
        if (backendStore.getSnapshot().synchronization.status !== "ready") return false;
        promise = action();
        return true;
    }, "操作前の状態同期");
    await promise;
}
async function post(path: string, body = {}) {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    require(response.ok, `検証APIの失敗: ${path}`);
    return response.json();
}
async function requestCount(key: string): Promise<number> {
    return (await (await fetch("/_test/metrics")).json()).requests[key] ?? 0;
}
const meterSettings = (id: string) => backendStore.getSnapshot().settings[id] as PowerMeterSetting;
const cameraSettings = (id: string) => backendStore.getSnapshot().settings[id] as CameraSetting;

async function verify() {
    const checks: string[] = [];
    await applicationRuntime.start();
    await waitFor(() => document.querySelector('[aria-label="P1のビュー"]'), "通常画面");
    const initialViews = ["devices", "image", "camera-settings", "measurement-recording", "graph", "power-settings"];
    const initialPositions = initialViews.map((id, index) => {
        require(element<HTMLSelectElement>(`[aria-label="P${index + 1}のビュー"]`).value === id, "初期6ビューの配置が違います。");
        return panelElement(`P${index + 1}`).getBoundingClientRect();
    });
    require(initialPositions[0].left < initialPositions[1].left && initialPositions[1].left < initialPositions[2].left
        && initialPositions[0].top < initialPositions[3].top && initialPositions.every((rect, index) => Math.abs(rect.left - initialPositions[index % 3].left) < 2), "2行3列の配置が違います。");
    for (let boundary = 0; boundary < 2; boundary++) {
        const before = panelElement("P2").getBoundingClientRect();
        await dragSplit(".mosaic-split.-row", boundary === 0 ? 25 : -25, boundary);
        const after = panelElement("P2").getBoundingClientRect();
        require(Math.abs(after.width - before.width) > 2, "列の境界を移動できません。");
        await dragSplit(".mosaic-split.-row", boundary === 0 ? -25 : 25, boundary);
    }
    for (let column = 0; column < 3; column++) {
        const before = initialViews.map((_, index) => panelElement(`P${index + 1}`).getBoundingClientRect());
        await dragSplit(".mosaic-split.-column", 25, column);
        const after = initialViews.map((_, index) => panelElement(`P${index + 1}`).getBoundingClientRect());
        require(after[column].height > before[column].height + 2 && after[column + 3].height < before[column + 3].height - 2, "対象列の上下の高さを変更できません。");
        after.forEach((rect, index) => {
            if (index % 3 !== column) require(Math.abs(rect.top - before[index].top) < 1 && Math.abs(rect.height - before[index].height) < 1
                && Math.abs(rect.left - before[index].left) < 1 && Math.abs(rect.width - before[index].width) < 1, "他の列の寸法が変わりました。");
            require(Math.abs(rect.left - after[index % 3].left) < 1 && Math.abs(rect.width - after[index % 3].width) < 1, "上下の縦境界がずれました。");
        });
        await dragSplit(".mosaic-split.-column", -25, column);
    }
    await layout("1パネル"); await layout("2行3列");
    initialViews.forEach((id, index) => require(element<HTMLSelectElement>(`[aria-label="P${index + 1}のビュー"]`).value === id, "配置切替でビューが消えました。"));
    checks.push("指定6ビュー、共通の縦境界と列別の横境界、他列の寸法不変、配置切替後のビュー保持を確認");
    await view("P1", "devices");
    await waitFor(() => !button("P1", "探索").disabled, "探索準備");
    button("P1", "探索").click();
    button("P1", "探索").click();
    await waitFor(() => (backendStore.getSnapshot().devices?.length ?? 0) >= 3, "機器探索");
    require(await requestCount("POST /api/devices/scan") === 1, "探索要求を重複送信しました。");
    const devices = backendStore.getSnapshot().devices!;
    const meters = devices.filter((device) => device.type === "power_meter");
    const camera = devices.find((device) => device.type === "camera")!;
    require(meters.length >= 2 && camera, "必要なダミー機器がありません。");
    const cameraOptions = [...element<HTMLSelectElement>('[aria-label="P3の対象機器"]').options].filter(option => option.value);
    const powerOptions = [...element<HTMLSelectElement>('[aria-label="P6の対象機器"]').options].filter(option => option.value);
    require(cameraOptions.length === 1 && cameraOptions[0].value === camera.id
        && powerOptions.length === meters.length && powerOptions.every(option => meters.some(meter => meter.id === option.value)), "設定ビューの機器種類が混ざっています。");
    await layout("1パネル");
    for (const device of [meters[0], meters[1], camera]) {
        panelElement("P1").querySelector<HTMLButtonElement>(`[data-device-id="${device.id}"]`)!.click();
        await pause();
        await click("P1", "接続");
        await waitFor(() => backendStore.getSnapshot().devices?.find((item) => item.id === device.id)?.status === "connected", "接続の反映");
        require(await requestCount(`POST /api/devices/${device.id}/connect`) === 1, "接続要求を重複送信しました。");
    }
    require(!panelElement("P1").querySelector(".view-message"), "通常の接続後に説明・成功メッセージが残っています。");
    require(!panelElement("P1").querySelector(".operation-reason"), "ボタン横に操作不可の理由文が残っています。");
    const list = panelElement("P1").querySelector<HTMLElement>(".device-list")!;
    const initialTop = list.getBoundingClientRect().top;
    const beforePollingAt = backendStore.getSnapshot().synchronization.lastUpdatedAt;
    const positions: number[] = [];
    const observer = new MutationObserver(() => positions.push(list.getBoundingClientRect().top));
    observer.observe(panelElement("P1"), { childList: true, subtree: true, characterData: true, attributes: true });
    await pause(1200);
    await waitFor(() => backendStore.getSnapshot().synchronization.lastUpdatedAt !== beforePollingAt, "周期取得の完了");
    observer.disconnect();
    require(positions.every((top) => Math.abs(top - initialTop) < 1), "周期取得で機器一覧の配置が変化しました。");
    require(!panelElement("P1").textContent?.includes("状態を再取得"), "周期取得の説明文が残っています。");
    checks.push("通常画面で探索・機器情報表示・接続。探索連打でもHTTPは1回");
    checks.push("接続後の常設説明・成功文を表示せず、周期取得中も機器一覧の位置を維持");

    const meter = meters[0].id;
    await view("P1", "measurement-recording");
    const samplingStart = [...panelElement("P1").querySelectorAll<HTMLButtonElement>(`[data-measurement-device="${meter}"] button`)]
        .find(node => node.textContent === "取得開始")!;
    const refreshButton = document.querySelector<HTMLButtonElement>(".backend-status-summary button")!;
    await waitFor(() => !samplingStart.disabled && !refreshButton.disabled, "周期更新の検証準備");
    const disabledChanges: boolean[] = [];
    const pollingObserver = new MutationObserver(records => {
        for (const record of records) if (record.attributeName === "disabled" && (record.target === samplingStart || record.target === refreshButton)) {
            disabledChanges.push((record.target as HTMLButtonElement).disabled);
        }
    });
    pollingObserver.observe(document.querySelector(".workspace-shell")!, { subtree: true, attributes: true, attributeFilter: ["disabled"] });
    const pollingFetch = window.fetch;
    let pollingCount = 0;
    window.fetch = async (...args: Parameters<typeof fetch>) => {
        if (String(args[0]).endsWith("/api/measurements/status")) { pollingCount++; await pause(150); }
        return pollingFetch(...args);
    };
    try { await waitFor(() => pollingCount >= 3, "3回の周期更新"); await pause(250); }
    finally { window.fetch = pollingFetch; pollingObserver.disconnect(); }
    require(disabledChanges.length === 0, `通常の周期更新でボタンの無効状態が切り替わりました: ${disabledChanges}`);
    require(!panelElement("P1").textContent?.includes("状態が未確認"), "周期更新で未確認の説明が表示されました。");
    window.fetch = async (...args: Parameters<typeof fetch>) => {
        if (String(args[0]).endsWith("/api/measurements/status")) await pause(300);
        return pollingFetch(...args);
    };
    try {
        refreshButton.click(); await pause();
        require(refreshButton.disabled, "手動再取得中のボタンが無効になりません。");
        await waitFor(() => !refreshButton.disabled, "手動再取得の完了");
    } finally { window.fetch = pollingFetch; }
    checks.push("150msの遅延を含む3回の周期更新で、取得開始・状態再取得ボタンの無効状態が変化しない");
    await view("P1", "device");
    await target("P1", meter);
    await waitFor(() => document.querySelector("#P1-wavelength"), "パワーメーター設定");
    setValue("#P1-wavelength", "");
    await pause();
    require(button("P1", "設定を適用").disabled, "空欄で設定を適用できます。");
    setValue("#P1-wavelength", "633");
    setValue("#P1-range", "0.02");
    setValue("#P1-averaging", "4");
    setValue("#P1-bandwidth", "high");
    element<HTMLInputElement>("#P1-auto_range").click();
    await click("P1", "設定を適用");
    await waitFor(() => meterSettings(meter)?.wavelength === 633 && meterSettings(meter)?.averaging === 4, "設定適用");
    require(meterSettings(meter).range === 0.02 && meterSettings(meter).bandwidth === "high", "設定項目が適用されていません。");
    checks.push("パワーメーターの数値・チェック・選択項目の適用、空欄の拒否");

    await layout("左右2パネル");
    await view("P2", "device");
    await target("P2", meter);
    require(element<HTMLInputElement>("#P2-wavelength").value === "633", "適用済み設定が共有されていません。");
    setValue("#P1-wavelength", "635");
    await pause(1100);
    require(element<HTMLInputElement>("#P1-wavelength").value === "635", "周期取得で入力が上書きされました。");
    setValue("#P2-range", "0.03");
    setValue("#P2-averaging", "8");
    await click("P2", "設定を適用");
    await waitFor(() => meterSettings(meter).range === 0.03 && meterSettings(meter).averaging === 8, "別パネルの設定適用");
    require(element<HTMLInputElement>("#P1-wavelength").value === "635", "別パネルの適用で入力が失われました。");
    require(panelElement("P1").textContent?.includes("別の操作で更新"), "外部の設定更新が通知されません。");
    await click("P1", "設定を適用");
    await waitFor(() => meterSettings(meter).wavelength === 635, "編集項目の結合");
    require(meterSettings(meter).range === 0.03 && meterSettings(meter).averaging === 8, "未編集の項目を古い値に戻しました。");
    setValue("#P1-wavelength", "640");
    await target("P1", meters[1].id);
    setValue("#P1-wavelength", "800");
    await target("P1", meter);
    require(element<HTMLInputElement>("#P1-wavelength").value === "640", "機器別入力が混ざりました。");
    await view("P1", "log");
    await layout("1パネル");
    await layout("上下2パネル");
    await view("P1", "device");
    require(element<HTMLInputElement>("#P1-wavelength").value === "640", "ビュー切り替えで入力が失われました。");
    await click("P1", "元に戻す");
    require(element<HTMLInputElement>("#P1-wavelength").value === "635", "最新の適用値へ戻せません。");
    checks.push("2パネル共有、機器別の入力保持、周期更新との共存、最新値への差分適用・元に戻す");

    await target("P1", camera.id);
    setValue("#P1-exposure", "12.5");
    setValue("#P1-gain", "2");
    setValue("#P1-fps", "15");
    element<HTMLInputElement>("#P1-gain_boost").click();
    await click("P1", "設定を適用");
    await waitFor(() => cameraSettings(camera.id)?.exposure === 0.0125 && cameraSettings(camera.id)?.fps === 15, "カメラ設定適用");
    require(cameraSettings(camera.id).gain_boost, "ゲインブーストが適用されません。");
    setValue("#P1-fps", "14");
    await readyAction(() => backendStore.startSampling(camera.id));
    const { directory } = await post("/_test/control", { command: "directory" });
    await readyAction(() => backendStore.startRecording({ directory, name: `device-ui-${Date.now()}`, video_options: { [camera.id]: { format: "ffv1" } } }));
    await waitFor(() => element<HTMLInputElement>("#P1-fps").disabled, "FPSの設定ロック");
    require(button("P1", "設定を適用").disabled, "記録中のFPS変更を適用できます。");
    await click("P1", "元に戻す");
    const recordingPutKey = `PUT /api/devices/${camera.id}/settings`;
    const beforeRecordingChanges = await requestCount(recordingPutKey);
    for (let step = 1; step <= 10; step++) {
        setValue("#P1-gain", String(2 + step / 10));
        await pause(45);
    }
    await waitFor(() => cameraSettings(camera.id).gain === 3, "記録中のゲイン変更");
    const recordingChanges = await requestCount(recordingPutKey) - beforeRecordingChanges;
    require(recordingChanges >= 2 && recordingChanges < 10, "記録中の連続操作が間隔送信になっていません。");
    require(backendStore.getSnapshot().recording!.is_recording, "設定変更で記録が止まりました。");
    require((backendStore.getSnapshot().recording!.targets[camera.id].settings_at_start as CameraSetting).gain === 2, "開始時設定が書き換わりました。");
    await readyAction(() => backendStore.stopRecording());
    await readyAction(() => backendStore.stopSampling(camera.id));
    checks.push(`カメラのms・秒変換、FPSロック、10回のゲイン入力を${recordingChanges}回へ集約、開始時設定を保持してFFV1動画を保存`);

    const putKey = `PUT /api/devices/${camera.id}/settings`;
    await post("/_test/fail-next-settings", { id: camera.id });
    setValue("#P1-gain", "4");
    await waitFor(() => panelElement("P1").textContent?.includes("試験用の設定適用失敗"), "設定失敗の表示");
    require(element<HTMLInputElement>("#P1-gain").value === "4", "失敗時に入力が消えました。");
    require(cameraSettings(camera.id).gain === 3, "失敗した値を適用済みにしました。");
    const failedCount = await requestCount(putKey);
    await applicationRuntime.refresh();
    await pause(1100);
    require(await requestCount(putKey) === failedCount, "失敗した変更を自動再送しました。");
    await click("P1", "即時項目を再適用");
    await waitFor(() => cameraSettings(camera.id).gain === 4, "明示的な再適用");
    await post("/_test/corrupt-next-settings");
    setValue("#P1-gain", "5");
    await waitFor(() => panelElement("P1").textContent?.includes("適用を確認できませんでした"), "結果不明の表示");
    require(element<HTMLInputElement>("#P1-gain").value === "5", "結果不明で入力が消えました。");
    const unknownCount = await requestCount(putKey);
    await applicationRuntime.refresh();
    await pause(1100);
    require(cameraSettings(camera.id).gain === 5 && await requestCount(putKey) === unknownCount, "結果不明の操作を再送しました。");
    await click("P1", "元に戻す");
    checks.push("HTTP失敗・結果不明で入力と確定値を区別し、状態確認後も自動再送しない");

    await target("P2", camera.id);
    const beforeSlow = await requestCount(putKey);
    await post("/_test/delay-next-settings", { milliseconds: 700 });
    setValue("#P1-exposure", "15");
    setValue("#P1-gain", "6");
    await waitFor(() => backendStore.getSnapshot().requests[`device:${camera.id}/settings`]?.pending, "遅い即時反映の開始");
    require(!element<HTMLInputElement>("#P1-gain").disabled, "即時反映中に編集できません。");
    setValue("#P1-fps", "16");
    setValue("#P1-exposure", "20");
    setValue("#P1-gain", "7");
    setValue("#P2-gain", "8");
    await pause(200);
    require(await requestCount(putKey) === beforeSlow + 1, "同一機器への送信が重なりました。");
    require(element<HTMLInputElement>("#P1-exposure").value === "20", "低速応答中の操作値を保持していません。");
    await waitFor(() => cameraSettings(camera.id).exposure === 0.02 && cameraSettings(camera.id).gain === 8, "最後の即時操作値");
    require(element<HTMLInputElement>("#P1-fps").value === "16" && cameraSettings(camera.id).fps === 15, "manualの入力が即時反映へ混ざりました。");
    require(await requestCount(putKey) === beforeSlow + 3, "待機中の各項目の最新値へ集約されていません。");
    const trace = await (await fetch("/_test/settings-writes")).json();
    require(trace.max_active === 1, "機器設定の並行送信が発生しました。");
    checks.push(`150ms間隔・700ms遅延応答で2パネルの露光/ゲイン変更を3回へ集約、最大並行${trace.max_active}件。manualのFPSは未送信`);
    await click("P1", "元に戻す");

    const beforeRevert = await requestCount(putKey);
    await post("/_test/delay-next-settings", { milliseconds: 500 });
    setValue("#P1-gain", "9");
    await waitFor(() => backendStore.getSnapshot().requests[`device:${camera.id}/settings`]?.pending, "戻し入力前の送信開始");
    setValue("#P1-gain", "8");
    await pause(200);
    require(element<HTMLInputElement>("#P1-gain").value === "8", "先行応答で戻し入力が消えました。");
    await waitFor(() => cameraSettings(camera.id).gain === 8 && !button("P1", "設定を取得").disabled, "通信中に元の値へ戻した最終操作");
    require(await requestCount(putKey) === beforeRevert + 2, "通信中の戻し入力を未変更として捨てました。");
    checks.push("先行送信中に確認済み値へ戻した入力も、先行応答で消さず最終値として反映");

    const beforeInvalid = await requestCount(putKey);
    setValue("#P1-exposure", "");
    await pause(250);
    require(await requestCount(putKey) === beforeInvalid, "数値の空欄を送信しました。");
    setValue("#P1-exposure", "25");
    await view("P1", "log");
    await pause(250);
    require(await requestCount(putKey) === beforeInvalid, "非表示後に待機分を送信しました。");
    await view("P1", "device");
    require(element<HTMLInputElement>("#P1-exposure").value === "25", "非表示時の入力が消えました。");
    await pause(250);
    require(await requestCount(putKey) === beforeInvalid, "再表示で自動再送しました。");
    await click("P1", "即時項目を再適用");
    await waitFor(() => cameraSettings(camera.id).exposure === 0.025, "取消し後の明示適用");
    const beforeSwitch = await requestCount(putKey);
    setValue("#P1-gain", "9");
    await target("P1", meter);
    await pause(250);
    require(await requestCount(putKey) === beforeSwitch, "対象変更後に旧機器へ送信しました。");
    await target("P1", camera.id);
    require(element<HTMLInputElement>("#P1-gain").value === "9", "対象変更で入力が失われました。");
    await click("P1", "元に戻す");
    checks.push("不正値の未送信、ビュー非表示・対象切替で待機分を取消し、再表示で再送せず入力だけを復元");

    preferencesStore.update({ theme: "light" });
    await pause();
    const section = panelElement("P1").querySelector<HTMLElement>(".device-section")!;
    require(getComputedStyle(section).backgroundColor === "rgb(235, 235, 235)", "ライト配色が反映されません。");
    await layout("1パネル");
    document.querySelector<HTMLElement>(".application")!.style.width = "420px";
    await pause();
    require(!panelElement("P1").querySelector(".workspace-panel-footer"), "パネルのフッターが残っています。");
    require(button("P1", "設定を適用").getClientRects().length > 0, "狭い幅で操作欄が消えました。");
    checks.push("ライト配色と狭いパネルでも本文・操作欄を維持");

    document.querySelector<HTMLElement>(".application")!.style.width = "";
    await layout("左右2パネル");
    await view("P1", "measurement-recording");
    await view("P2", "measurement-recording");
    setValue('[aria-label="P2の保存先"]', "別パネルの保存先");
    setValue('[aria-label="P2の記録名"]', "別パネルの入力");
    async function acquisition(id: string, label: string) {
        await waitFor(() => {
            const row = panelElement("P1").querySelector(`[data-measurement-device="${id}"]`)!;
            const action = [...row.querySelectorAll<HTMLButtonElement>("button")].find(node => node.textContent === label)!;
            if (action.disabled) return false;
            action.click(); return true;
        }, label);
        await waitFor(() => !Object.values(backendStore.getSnapshot().requests).some(request => request.pending > 0 && request.writing), "取得操作の完了");
        await pause();
    }
    const beforeFiles = (await (await fetch("/_test/recording-files")).json()).files.length;
    await acquisition(meter, "取得開始");
    await acquisition(camera.id, "取得開始");
    await waitFor(() => panelElement("P1").querySelectorAll("time").length >= 2, "数値と画像の最終受信");
    require((await (await fetch("/_test/recording-files")).json()).files.length === beforeFiles, "取得だけで保存しました。");
    setValue('[aria-label="P1の保存先"]', directory);
    const runName = `measurement-ui-${Date.now()}`;
    setValue('[aria-label="P1の記録名"]', runName);
    await pause();
    require(button("P1", "記録開始").disabled, "カメラ形式を未選択で開始できます。");
    setValue(`[aria-label="P1の${camera.id}の動画形式"]`, "ffv1");
    await click("P1", "記録開始");
    await waitFor(() => backendStore.getSnapshot().recording?.is_recording, "通常ビューの記録開始");
    require(Object.keys(backendStore.getSnapshot().recording!.targets).length === 2, "記録開始時の対象が不正です。");
    await waitFor(() => element<HTMLInputElement>('[aria-label="P2の保存先"]').matches(":disabled"), "他パネルの記録条件ロック");
    await acquisition(meters[1].id, "取得開始");
    require(!backendStore.getSnapshot().recording!.targets[meters[1].id], "後から取得した機器を記録へ追加しました。");
    const activeRecording = backendStore.getSnapshot().recording!.recording_id;
    const imageMetricsBefore = await (await fetch("/_test/metrics")).json();
    await view("P1", "image"); await target("P1", camera.id);
    await view("P2", "image"); await target("P2", camera.id);
    const imageNode = (panel: string) => panelElement(panel).querySelector<HTMLImageElement>(`img[alt="${panel}のカメラプレビュー"]`)!;
    await waitFor(() => imageNode("P1")?.naturalWidth === 640 && imageNode("P2")?.naturalHeight === 480, "実カメラJPEGのデコード");
    require(getComputedStyle(imageNode("P1")).objectFit === "contain", "画像がパネル内へ収まりません。");
    const beforeImageUrls = previewUrlsCreated;
    await pause(600);
    require(previewUrlsCreated > beforeImageUrls + 4 && previewUrls.size === 2 && previewUrlsPeak <= 2, "画像更新でURLの保持数が増加しました。");
    setValue('[aria-label="P1の画像倍率"]', "2");
    await pause();
    require(Math.abs(imageNode("P1").getBoundingClientRect().width - 1280) < 1, "200%表示の幅が不正です。");
    require(element<HTMLSelectElement>('[aria-label="P2の画像倍率"]').value === "fit", "パネル間で画像倍率が混ざりました。");
    const imageViewport = panelElement("P1").querySelector<HTMLElement>(".image-preview-viewport")!;
    require(imageViewport.scrollWidth > imageViewport.clientWidth, "拡大画像をスクロールできません。");
    await target("P1", "");
    await waitFor(() => previewUrls.size === 1, "対象解除時のURL解放");
    await target("P1", camera.id);
    require(element<HTMLSelectElement>('[aria-label="P1の画像倍率"]').value === "2", "対象切替で倍率が消えました。");
    require(backendStore.getSnapshot().recording!.recording_id === activeRecording && backendStore.getSnapshot().recording!.is_recording, "画像表示で記録が変化しました。");
    const imageMetricsAfter = await (await fetch("/_test/metrics")).json();
    require(imageMetricsBefore.connections === imageMetricsAfter.connections, "画像パネルで受信接続を追加しました。");
    require(JSON.stringify(Object.entries(imageMetricsBefore.requests).filter(([key]) => !key.startsWith("GET ")))
        === JSON.stringify(Object.entries(imageMetricsAfter.requests).filter(([key]) => !key.startsWith("GET "))), "倍率操作で機器操作を送信しました。");
    await view("P1", "table"); await target("P1", meter);
    await view("P2", "graph");
    await waitFor(() => previewUrls.size === 0, "画像ビュー非表示時の全URL解放");
    await waitFor(() => document.querySelector('[aria-label="P2のグラフ時間幅"]'), "グラフの遅延読込み");
    await target("P2", meter);
    await waitFor(() => chartLines(panelElement("P2")).length, "数値グラフ描画");
    const lastRow = panelElement("P1").querySelector(".measurement-data-table tbody tr:last-child")!;
    const rowElapsed = Number(lastRow.children[1].textContent);
    const matching = measurementStore.getSnapshot().measurements[meter].read().find(event => event.elapsed_s === rowElapsed)!;
    require(matching && Number(lastRow.children[2].textContent) === matching.data.power_w, "テーブルの数値が受信値と一致しません。");
    require(lastRow.querySelector("time")?.dateTime === matching.datetime, "テーブルの取得日時が受信値と一致しません。");
    setValue('[aria-label="P2のグラフ時間幅"]', "10");
    await pause();
    require(backendStore.getSnapshot().recording!.recording_id === activeRecording && backendStore.getSnapshot().recording!.is_recording, "表・グラフ表示で記録が変化しました。");
    setValue('[aria-label="P2の比較機器"]', meters[1].id);
    await waitFor(() => chartLines(panelElement("P2")).length === 2, "取得・記録中の2系列表示");
    await backendStore.refreshSettings(meter);
    require(backendStore.getSnapshot().recording!.recording_id === activeRecording, "比較表示で記録が切り替わりました。");
    panelElement("P2").querySelector<HTMLButtonElement>(`[aria-label="P2の系列${meters[1].id}を削除"]`)!.click();
    checks.push("2台の実ダミー取得・記録中に比較系列を追加し、設定GETと系列削除を確認");
    checks.push("実ダミー受信の表の時刻・経過秒・パワー値を照合し、Chart.js描画中も取得・記録を継続");
    checks.push("ダミーカメラJPEGを2パネルでライブ表示、fit/200%とスクロール・倍率保持、URL最大2件・非表示時0件、追加の変更HTTP/WebSocketなし");
    await view("P1", "log"); await layout("1パネル"); await pause(400);
    require(backendStore.getSnapshot().recording!.recording_id === activeRecording && backendStore.getSnapshot().recording!.is_recording, "ビュー変更で記録が止まりました。");
    await layout("上下2パネル"); await view("P1", "measurement-recording");
    await post("/_test/control", { command: "delay-stop" });
    await click("P1", "記録停止");
    await waitFor(() => backendStore.getSnapshot().requests["recording/stop"]?.pending, "停止要求");
    await waitFor(() => backendStore.getSnapshot().recording?.is_stopping, "終了処理中の状態取得");
    require(button("P1", "記録開始").disabled, "終了処理中に開始できます。");
    await post("/_test/control", { command: "release-stop" });
    await waitFor(() => !backendStore.getSnapshot().recording?.is_stopping && !element<HTMLInputElement>('[aria-label="P1の保存先"]').matches(":disabled"), "保存完了確認");
    require(backendStore.getSnapshot().measurement!.sampling_device_ids.length === 3, "記録停止で取得が止まりました。");
    require(Object.values(backendStore.getSnapshot().recording!.targets).every(target => target.state === "completed"), "対象の保存が完了していません。");
    await view("P2", "measurement-recording");
    require(element<HTMLInputElement>('[aria-label="P2の記録名"]').value === "別パネルの入力", "他パネルの保存条件が消えました。");
    await click("P1", "記録開始");
    await waitFor(() => panelElement("P1").querySelector('[role="alert"]'), "作成済み記録名の衝突");
    await applicationRuntime.refresh();
    setValue('[aria-label="P1の記録名"]', `${runName}-retry`);
    await click("P1", "記録開始");
    await waitFor(() => backendStore.getSnapshot().recording?.is_recording, "2回目の開始");
    for (const id of [meter, meters[1].id, camera.id]) await acquisition(id, "取得停止");
    require(backendStore.getSnapshot().recording!.is_recording, "全取得停止を記録終了として扱いました。");
    await post("/_test/fail-final-json");
    await click("P1", "記録停止");
    await waitFor(() => !!backendStore.getSnapshot().recording?.error || backendStore.getSnapshot().synchronization.status === "error", "最終JSON失敗");
    await applicationRuntime.refresh();
    await click("P1", "停止を再試行");
    await waitFor(() => backendStore.getSnapshot().requests["recording/stop"]?.pending === 0
        && !backendStore.getSnapshot().requests["recording/stop"]?.error
        && !element<HTMLInputElement>('[aria-label="P1の保存先"]').matches(":disabled"), "最終JSON保存の再試行");
    checks.push("測定・記録ビューで取得と記録を独立操作、後発機器を対象へ追加せず、2パネル入力保持・遅い停止・記録名衝突・全取得停止後の記録継続・最終JSON失敗の明示再試行を確認");
    await acquisition(meter, "取得開始");
    await acquisition(camera.id, "取得開始");
    setValue('[aria-label="P1の記録名"]', `${runName}-partial`);
    await post("/_test/fail-next-video-start");
    await click("P1", "記録開始");
    await waitFor(() => backendStore.getSnapshot().synchronization.status === "error", "部分的な開始失敗");
    await applicationRuntime.refresh();
    require(backendStore.getSnapshot().recording!.targets[camera.id].state === "failed", "失敗対象を成功にしました。");
    await waitFor(() => panelElement("P1").textContent?.includes("試験用の動画初期化失敗"), "対象の初期化失敗の描画");
    await acquisition(meter, "取得停止");
    await acquisition(camera.id, "取得停止");
    checks.push("開始の部分失敗を対象別の保存失敗・全体エラーとして表示し、取得を独立して停止");
    const beforeDisplayMetrics = await (await fetch("/_test/metrics")).json();
    const savedCameraPreview = measurementStore.getSnapshot().previews[camera.id];
    const base = measurementStore.getSnapshot().measurements[meter].at(-1)!;
    measurementStore.setSession(base.server_instance_id, "display-reset");
    measurementStore.setSession(base.server_instance_id, base.measurement_id);
    // 全件保持と表示件数を、取得停止後の検証用数値で確認する。
    for (let index = 0; index < 1050; index++) measurementStore.receive({ ...base, elapsed_s: 1000 + index,
        timestamp_ns: base.timestamp_ns + index * 1e9, data: { power_w: index - 500, other: index * 2, label: "数値以外" } });
    await view("P1", "table"); await target("P1", meter);
    setValue('[aria-label="P1のテーブル表示件数"]', "1000");
    await pause();
    await waitFor(() => panelElement("P1").querySelectorAll(".measurement-data-table tbody tr").length === 1000, "保持上限の表の描画");
    require(measurementStore.getSnapshot().measurements[meter].length === 1050, "共有履歴から古い点が失われました。");
    const follow = element<HTMLInputElement>('[aria-label="P1のテーブル追従"]'); follow.click();
    await pause();
    const scroll = panelElement("P1").querySelector<HTMLElement>(".measurement-table-scroll")!;
    const retainedRow = scroll.querySelectorAll("tbody tr")[500];
    const retainedElapsed = retainedRow.children[1].textContent;
    scroll.scrollTop = 100;
    measurementStore.receive({ ...base, elapsed_s: 2050, data: { power_w: 550, other: 2100, label: "数値以外" } });
    await waitFor(() => [...scroll.querySelectorAll("tbody tr")].some(row => row.children[1].textContent === "2050"), "循環後の最終行の表示");
    require(Math.abs(scroll.scrollTop - 100) < 2, "追従OFFでスクロール位置が変わりました。");
    require([...scroll.querySelectorAll("tbody tr")].find(row => row.children[1].textContent === retainedElapsed) === retainedRow,
        "上限で循環した際に、表示範囲内の既存行を作り直しました。");
    measurementStore.receive({ ...base, elapsed_s: 2050, data: { power_w: 551, other: 2102, label: "同時刻の別サンプル" } });
    await waitFor(() => [...scroll.querySelectorAll("tbody tr")].filter(row => row.children[1].textContent === "2050").length === 2, "同時刻の別サンプルの最終表示");
    const sameTimeRows = [...scroll.querySelectorAll("tbody tr")].filter(row => row.children[1].textContent === "2050");
    require(sameTimeRows.length === 2 && sameTimeRows[0] !== sameTimeRows[1]
        && sameTimeRows.map(row => row.children[2].textContent).join(",") === "550,551", "同時刻の別サンプルを同じ行にしました。");
    element<HTMLInputElement>('[aria-label="P1の列other"]').click();
    setValue('[aria-label="P1のテーブル表示件数"]', "500");
    await target("P1", meters[1].id); await target("P1", meter);
    require(element<HTMLSelectElement>('[aria-label="P1のテーブル表示件数"]').value === "500" && !element<HTMLInputElement>('[aria-label="P1の列other"]').checked,
        "機器切替で表示件数・列設定が消えました。");
    await view("P2", "table"); await target("P2", meter);
    require(element<HTMLSelectElement>('[aria-label="P2のテーブル表示件数"]').value === "100", "パネル間で表示件数が混ざりました。");
    await view("P1", "graph"); await target("P1", meter);
    setValue('[aria-label="P1のグラフ時間幅"]', "0");
    await pause();
    const canvas = panelElement("P1").querySelector<HTMLCanvasElement>(".measurement-chart-area canvas")!;
    require(canvas?.width > 100 && chartLines(panelElement("P1"))[0]?.count === 1052, "Canvasの元データが全履歴と一致しません。");
    require(![...element<HTMLSelectElement>('[aria-label="P1のグラフ項目"]').options].some(option => option.value === "label"), "非数値項目がグラフに含まれます。");
    for (let index = 0; index < 1050; index++) measurementStore.receive({ ...base, id: meters[1].id,
        elapsed_s: 1000.25 + index, data: { power_w: index } });
    setValue('[aria-label="P1の比較機器"]', meters[1].id);
    await waitFor(() => chartLines(panelElement("P1")).length === 2, "1000件ずつの2系列描画");
    const pair = chartLines(panelElement("P1"));
    require(pair.every((line, index) => line.count === (index === 0 ? 1052 : 1050)), "比較系列の元データ件数が違います。");
    require(pair[0].color !== pair[1].color, "系列の色が同じです。");
    element<HTMLInputElement>(`[aria-label="P1の系列${meter}"]`).click();
    await waitFor(() => chartLines(panelElement("P1")).length === 1, "主系列の非表示");
    element<HTMLInputElement>(`[aria-label="P1の系列${meter}"]`).click();
    await waitFor(() => chartLines(panelElement("P1")).length === 2, "主系列の再表示");
    await view("P1", "log"); await view("P1", "graph");
    require(chartLines(panelElement("P1")).length === 2, "再表示で比較系列が消えました。");
    panelElement("P1").querySelector<HTMLButtonElement>(`[aria-label="P1の系列${meters[1].id}を削除"]`)!.click();
    await pause();
    checks.push("2台の全履歴1052点・1050点、異なる取得時刻・色分け・系列ON/OFF・再表示時の選択保持を確認");
    await view("P1", "log"); await view("P1", "graph");
    require(element<HTMLSelectElement>('[aria-label="P1のグラフ時間幅"]').value === "0", "ビュー切替で時間幅が消えました。");
    await view("P2", "graph");
    require(element<HTMLSelectElement>('[aria-label="P2のグラフ時間幅"]').value === "10", "パネル間で時間幅が混ざりました。");
    await layout("上下2パネル");
    await dragSplit(".mosaic-split.-column", -10000);
    await pause();
    await dragSplit(".mosaic-split.-column", 25);
    await waitFor(() => panelElement("P1").querySelector(".measurement-chart-area canvas")?.getClientRects().length, "最小高さからグラフ再拡大");
    const afterDisplayMetrics = await (await fetch("/_test/metrics")).json();
    const writesOnly = (requests: Record<string, number>) => Object.fromEntries(Object.entries(requests).filter(([key]) => !key.startsWith("GET ")));
    require(JSON.stringify(writesOnly(beforeDisplayMetrics.requests)) === JSON.stringify(writesOnly(afterDisplayMetrics.requests)), "表示操作で変更APIを送信しました。");
    require(beforeDisplayMetrics.connections === afterDisplayMetrics.connections, "表示操作でWebSocketが増えました。");
    await post("/_test/control", { command: "offline" });
    await applicationRuntime.refresh().catch(() => {});
    await waitFor(() => panelElement("P1").querySelector(".view-message-error"), "グラフの通信異常表示");
    require(panelElement("P1").querySelector(".measurement-chart-area canvas")?.getClientRects().length, "通信断で最後の波形を消しました。");
    await post("/_test/control", { command: "online" });
    await applicationRuntime.refresh();
    const actualSession = backendStore.getSnapshot().measurement!;
    measurementStore.setSession(actualSession.server_instance_id, "verification-new-measurement");
    await waitFor(() => !panelElement("P1").querySelector(".measurement-chart-area canvas")?.getClientRects().length, "測定切替時の旧波形消去");
    require(element<HTMLSelectElement>('[aria-label="P1のグラフ時間幅"]').value === "0", "測定切替で表示設定が消えました。");
    measurementStore.setSession(actualSession.server_instance_id, actualSession.measurement_id);
    checks.push("検証用1,050イベントの全件保持と追加受信、表の直近1000件・追従OFF・列/機器/パネル別設定・全範囲描画・再拡大を確認。表示操作の変更HTTP/追加WebSocketなし");
    checks.push("通信異常時も波形を保持し、共有測定ID切替では旧波形を消去して時間幅設定を保持");
    // 測定切替の検査で消えた画像を、現在の確認済み測定の画像として検証用に復元する。
    measurementStore.receive(savedCameraPreview);
    await view("P1", "image"); await target("P1", camera.id);
    await waitFor(() => imageNode("P1")?.naturalWidth === 640, "取得停止後の最後の画像を復元");
    const stoppedImageUrl = imageNode("P1").src;
    await post("/_test/control", { command: "offline" });
    await applicationRuntime.refresh().catch(() => {});
    await waitFor(() => [...panelElement("P1").querySelectorAll<HTMLElement>(".view-message-error")].some(node => node.getClientRects().length), "画像の通信異常表示");
    require(imageNode("P1").src === stoppedImageUrl && imageNode("P1").naturalWidth === 640, "通信断で最後の画像が失われました。");
    await post("/_test/control", { command: "online" });
    await applicationRuntime.refresh();
    await dragSplit(".mosaic-split.-column", -10000);
    await waitFor(() => previewUrls.size === 0, "最小高さで画像URLを解放");
    await dragSplit(".mosaic-split.-column", 10000);
    await waitFor(() => imageNode("P1")?.naturalWidth === 640 && previewUrls.size === 1, "再拡大時の画像復元");
    require(element<HTMLSelectElement>('[aria-label="P1の画像倍率"]').value === "2", "再表示で倍率が失われました。");
    measurementStore.receive({ ...savedCameraPreview, timestamp_ns: String(BigInt(savedCameraPreview.timestamp_ns) + 1n), jpeg: new Blob(["broken"], { type: "image/jpeg" }) });
    await waitFor(() => panelElement("P1").textContent?.includes("プレビュー画像を読み込めません"), "画像デコードエラー");
    measurementStore.receive({ ...savedCameraPreview, timestamp_ns: String(BigInt(savedCameraPreview.timestamp_ns) + 2n) });
    await waitFor(() => imageNode("P1")?.naturalWidth === 640 && !imageNode("P1").hidden, "正常JPEGで画像表示を復旧");
    measurementStore.setSession(actualSession.server_instance_id, "verification-new-image-measurement");
    await waitFor(() => previewUrls.size === 0, "測定切替時の旧画像URL解放");
    measurementStore.setSession(actualSession.server_instance_id, actualSession.measurement_id);
    checks.push(`取得停止・通信断で最後の画像を保持、最小高さ/再拡大・デコード異常復旧・測定ID切替を確認。表示URL最大${previewUrlsPeak}件、最後は${previewUrls.size}件`);
    await view("P1", "devices");
    for (const device of [meters[0], meters[1], camera]) {
        panelElement("P1").querySelector<HTMLButtonElement>(`[data-device-id="${device.id}"]`)!.click();
        await pause();
        await click("P1", "切断");
        await waitFor(() => backendStore.getSnapshot().devices?.find((item) => item.id === device.id)?.status === "disconnected", "切断反映");
    }
    checks.push("通常画面の切断操作と全機器の後処理");
    require(errors.length === 0, errors.join("\n"));
    return checks;
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
void verify().then((checks) => ({ ok: true, checks }), (cause) => ({ ok: false, error: String(cause), stack: cause?.stack }))
    .then(async (result) => {
        applicationRuntime.dispose();
        disposeWebSocketClients();
        await fetch("/_test/result", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...result, errors }) });
    });
