import { backendStore } from "./backendStore.ts";
import type { createBackendStore } from "./backendStore.ts";
import { getOperationAvailability } from "./operationPolicy.ts";
import { getSettingsContract } from "../devices/settingsContracts.ts";
import { settingsEqual } from "../devices/settingsValues.ts";
import type { ApplyMode, SettingsInputs } from "../devices/settingsValues.ts";

export type SettingEdit = { value: string | boolean; revision: number };
export type SettingsEdits = Record<string, SettingEdit>;
export type SettingsProgress = { pending: boolean; applying: boolean };
type ClientOptions = {
    deviceId: string; session: string; type: string; blocked: boolean;
    onApplied: (edits: SettingsEdits) => void;
    onError: (message: string) => void;
    onProgress: (progress: SettingsProgress) => void;
};
type Client = { options: ClientOptions; active: boolean; blocked: boolean };
type Job = { client: Client; mode: ApplyMode; edits: SettingsEdits };
export const editValues = (edits: SettingsEdits): SettingsInputs => Object.fromEntries(Object.entries(edits).map(([name, edit]) => [name, edit.value]));

/** パネルの送信待ちを機器単位で直列化する。失敗・対象変更後は自動再送しない。 */
export function createSettingsUpdates(store: ReturnType<typeof createBackendStore>, interval = 150) {
    const clients = new Set<Client>();
    let jobs: Job[] = [];
    const running = new Map<string, Client>();
    const sent = new Map<Client, SettingsEdits>();
    const lastStarted = new Map<string, number>();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;

    function notify(client: Client) {
        if (client.active) client.options.onProgress({ pending: jobs.some((job) => job.client === client),
            applying: [...running.values()].includes(client) });
    }
    function cancel(client: Client) {
        jobs = jobs.filter((job) => job.client !== client);
        notify(client);
    }
    function fail(client: Client, message: string) {
        client.blocked = true;
        cancel(client);
        client.options.onError(message);
    }
    function validTarget(client: Client) {
        const state = store.getSnapshot();
        return state.measurement?.server_instance_id === client.options.session
            && state.devices?.some((device) => device.id === client.options.deviceId && device.type === client.options.type && device.status === "connected");
    }
    function schedule(delay = interval) {
        if (disposed || timer !== undefined || jobs.length === 0) return;
        timer = setTimeout(() => { timer = undefined; pump(); }, delay);
    }
    function pump() {
        const snapshot = store.getSnapshot();
        if (snapshot.synchronization.status !== "ready") return;
        for (const id of new Set(jobs.map((job) => job.client.options.deviceId))) {
            if (running.has(id)) continue;
            if (Object.entries(snapshot.requests).some(([key, request]) => request.pending > 0 && request.writing
                && (key.startsWith(`device:${id}/`) || key.startsWith("recording/")))) continue;
            const remaining = interval - (Date.now() - (lastStarted.get(id) ?? 0));
            if (remaining > 0) { schedule(remaining); continue; }
            const job = jobs.find((item) => item.client.options.deviceId === id)!;
            jobs = jobs.filter((item) => item !== job);
            const client = job.client;
            if (!client.active || client.blocked || !validTarget(client)) { notify(client); continue; }
            const contract = getSettingsContract(client.options.type)!;
            const current = store.getSnapshot().settings[id];
            try {
                if (!current) throw new Error("適用済み設定を取得してください。");
                const changes = editValues(job.edits);
                if (Object.keys(changes).some((name) => contract.modes[name] !== job.mode)) throw new Error("設定の適用方法が一致しません。");
                const settings = contract.merge(current, changes);
                const availability = getOperationAvailability(store.getSnapshot(), { operation: "updateSettings", deviceId: id, settings });
                if (!availability.allowed) throw new Error(availability.reason ?? "設定を適用できません。");
                if (settingsEqual(current, settings)) {
                    client.options.onApplied(job.edits);
                    notify(client);
                    continue;
                }
                running.set(id, client);
                sent.set(client, job.edits);
                lastStarted.set(id, Date.now());
                notify(client);
                void store.updateSettings(id, settings).then(() => {
                    if (store.getSnapshot().measurement?.server_instance_id === client.options.session) client.options.onApplied(job.edits);
                }).catch((cause: unknown) => {
                    fail(client, cause instanceof Error ? cause.message : String(cause));
                    for (const waiting of [...clients]) {
                        if (jobs.some((item) => item.client === waiting)) fail(waiting, "設定の送信を停止しました。状態を確認して再適用してください。");
                    }
                }).finally(() => {
                    running.delete(id);
                    sent.delete(client);
                    notify(client);
                    schedule(0);
                });
            } catch (cause) {
                fail(client, cause instanceof Error ? cause.message : String(cause));
            }
        }
        if (jobs.length && !running.size) schedule();
    }
    const unsubscribe = store.subscribe(() => {
        const state = store.getSnapshot();
        for (const client of clients) {
            const disconnecting = Object.entries(state.requests).some(([key, request]) => key === `device:${client.options.deviceId}/disconnect` && request.pending > 0);
            if (!validTarget(client) || disconnecting) cancel(client);
            else if (state.synchronization.status === "error" && jobs.some((job) => job.client === client)) {
                fail(client, "設定の送信を停止しました。状態を確認して再適用してください。");
            }
        }
        schedule();
    });

    return {
        createEditor(options: ClientOptions) {
            const client: Client = { options, active: true, blocked: options.blocked };
            clients.add(client);
            notify(client);
            return {
                isSending: (name: string) => sent.get(client)?.[name] !== undefined,
                replace(edits: SettingsEdits, mode: ApplyMode) {
                    jobs = jobs.filter((job) => job.client !== client || job.mode !== mode);
                    const unsent = Object.fromEntries(Object.entries(edits).filter(([name, edit]) => sent.get(client)?.[name]?.revision !== edit.revision));
                    if (client.active && !client.blocked && validTarget(client) && Object.keys(unsent).length) jobs.push({ client, mode, edits: unsent });
                    notify(client);
                    schedule();
                },
                flush() {
                    if (timer !== undefined) { clearTimeout(timer); timer = undefined; }
                    // 確定操作では待ち時間を短縮するが、進行中の同一機器への送信は追い越さない。
                    lastStarted.delete(options.deviceId);
                    schedule(0);
                },
                retry() { client.blocked = false; },
                cancel: () => cancel(client),
                dispose() { client.active = false; cancel(client); clients.delete(client); },
            };
        },
        dispose() {
            disposed = true;
            if (timer !== undefined) clearTimeout(timer);
            unsubscribe();
            for (const client of clients) client.active = false;
            clients.clear();
            jobs = [];
        },
    };
}

export const settingsUpdates = createSettingsUpdates(backendStore);
export type SettingsEditor = ReturnType<typeof settingsUpdates.createEditor>;
