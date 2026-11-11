import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { backendStore } from "../state/backendStore";
import { editValues, settingsUpdates } from "../state/settingsUpdates";
import type { SettingsEditor, SettingsEdits, SettingsProgress } from "../state/settingsUpdates";
import type { DeviceSettings } from "../type/devices";
import { DeviceDetails, DeviceSelector, DeviceStatus } from "../components/DeviceInfo";
import { ViewLayout, ViewMessage } from "../components/View";
import { OperationButton } from "../components/OperationButton";
import { getSettingsContract } from "../devices/settingsContracts";
import type { SettingsType } from "../devices/settingsContracts";
import { settingsForms } from "../devices/settingsForms";
import { settingsEqual } from "../devices/settingsValues";
import type { ApplyMode } from "../devices/settingsValues";
import "./devices.css";

type Draft = { baseline: DeviceSettings; changes: SettingsEdits };
type Message = { error: boolean; text: string };

/** 機器・セッション別の入力を保持し、登録したフォームと共通送信制御を接続する。 */
export function DeviceSettingsView({ panelId, deviceType }: { panelId: string; deviceType?: SettingsType }) {
    const backend = useSyncExternalStore(backendStore.subscribe, backendStore.getSnapshot);
    const [selectedId, setSelectedId] = useState("");
    const [drafts, setDrafts] = useState<Record<string, Draft>>({});
    const draftsRef = useRef(drafts);
    const revision = useRef(0);
    const editor = useRef<SettingsEditor | null>(null);
    const [messages, setMessages] = useState<Record<string, Message>>({});
    const messagesRef = useRef(messages);
    const [progress, setProgress] = useState<SettingsProgress>({ pending: false, applying: false });
    const [reading, setReading] = useState(false);
    const devices = (backend.devices ?? []).filter(device => !deviceType || device.type === deviceType);
    const selected = devices.find((device) => device.id === selectedId);
    const settings = backend.settings[selectedId];
    const type = selected?.type ?? "";
    const contract = getSettingsContract(type);
    const Form = Object.hasOwn(settingsForms, type) ? settingsForms[type as SettingsType] : undefined;
    const session = backend.measurement?.server_instance_id ?? "";
    const scope = JSON.stringify([session, selectedId]);
    const draft = drafts[scope];
    const locked = backend.recording?.targets[selectedId]?.locked_settings ?? [];
    const writing = Object.entries(backend.requests).some(([key, request]) => key.startsWith(`device:${selectedId}/`) && request.pending > 0 && request.writing);
    const otherWriting = Object.entries(backend.requests).some(([key, request]) => key.startsWith(`device:${selectedId}/`) && !key.endsWith("/settings") && request.pending > 0 && request.writing);
    const disabled = selected?.status !== "connected" || reading || otherWriting;
    const message = messages[scope];

    const updateDraft = useCallback((targetScope: string, change: (previous: Draft | undefined) => Draft | undefined) => {
        const next = { ...draftsRef.current };
        const value = change(next[targetScope]);
        if (value && Object.keys(value.changes).length) next[targetScope] = value;
        else delete next[targetScope];
        draftsRef.current = next;
        setDrafts(next);
    }, []);
    const updateMessage = useCallback((targetScope: string, value?: Message) => {
        const next = { ...messagesRef.current };
        if (value) next[targetScope] = value;
        else delete next[targetScope];
        messagesRef.current = next;
        setMessages(next);
    }, []);

    useEffect(() => {
        if (!selectedId || !session || !contract || selected?.status !== "connected") return;
        const instance = settingsUpdates.createEditor({ deviceId: selectedId, session, type,
            blocked: messagesRef.current[scope]?.error ?? false,
            onProgress: setProgress,
            onApplied: (edits) => {
                updateDraft(scope, (previous) => {
                    if (!previous) return undefined;
                    const changes = { ...previous.changes };
                    for (const [name, sent] of Object.entries(edits)) {
                        if (changes[name]?.revision === sent.revision) delete changes[name];
                    }
                    return { baseline: backendStore.getSnapshot().settings[selectedId] ?? previous.baseline, changes };
                });
            },
            onError: (text) => updateMessage(scope, { error: true, text: `設定の適用を確認できませんでした。${text}` }),
        });
        editor.current = instance;
        return () => { instance.dispose(); editor.current = null; };
    }, [selectedId, session, type, scope, contract, selected?.status, updateDraft, updateMessage]);

    function modeEdits(mode: ApplyMode, validOnly = false, changes = draft?.changes ?? {}): SettingsEdits {
        if (!contract || !settings) return {};
        return Object.fromEntries(Object.entries(changes).filter(([name, edit]) => {
            if (contract.modes[name] !== mode) return false;
            if (!validOnly) return true;
            try { contract.merge(settings, { [name]: edit.value }); return !locked.includes(name); }
            catch { return false; }
        }));
    }
    const manual = modeEdits("manual");
    const immediate = modeEdits("immediate");
    let candidate: DeviceSettings | undefined;
    let manualError = "";
    let validationError = "";
    if (settings && contract) {
        try { candidate = contract.merge(settings, editValues(manual)); }
        catch (cause) { manualError = cause instanceof Error ? cause.message : String(cause); }
        try { contract.merge(settings, editValues(draft?.changes ?? {})); }
        catch (cause) { validationError = cause instanceof Error ? cause.message : String(cause); }
    }
    const changed = !!settings && !!candidate && !settingsEqual(settings, candidate);
    const immediateChanged = Object.keys(immediate).length > 0;
    const updatedElsewhere = !progress.applying && !!settings && !!draft && !settingsEqual(draft.baseline, settings);

    function edit(name: string, value: string | boolean) {
        if (!settings || !contract || locked.includes(name)) return;
        updateDraft(scope, (previous) => {
            const changes = { ...previous?.changes, [name]: { value, revision: ++revision.current } };
            if (value === contract.getInputs(settings)[name] && !editor.current?.isSending(name)) delete changes[name];
            return { baseline: previous?.baseline ?? settings, changes };
        });
        if (!messagesRef.current[scope]?.error) updateMessage(scope);
        if (contract.modes[name] === "immediate") editor.current?.replace(modeEdits("immediate", true, draftsRef.current[scope]?.changes ?? {}), "immediate");
    }
    function reset() {
        editor.current?.cancel();
        updateDraft(scope, () => undefined);
        updateMessage(scope);
        editor.current?.retry();
    }
    function apply(mode: ApplyMode) {
        editor.current?.retry();
        updateMessage(scope);
        editor.current?.replace(modeEdits(mode, mode === "immediate"), mode);
        editor.current?.flush();
    }
    async function refreshSettings() {
        setReading(true);
        try { await backendStore.refreshSettings(selectedId); }
        catch (cause) { updateMessage(scope, { error: true, text: `設定を取得できません。${cause instanceof Error ? cause.message : String(cause)}` }); }
        finally { setReading(false); }
    }
    function changeTarget(id: string) {
        editor.current?.dispose();
        editor.current = null;
        setProgress({ pending: false, applying: false });
        setSelectedId(id);
    }

    let content: ReactNode;
    if (!selected) content = <ViewMessage>{selectedId ? "選択した機器が一覧にありません。" : "設定する機器を選択してください。"}</ViewMessage>;
    else if (selected.status !== "connected") content = <ViewMessage tone={selected.status === "error" ? "error" : "info"}>{selected.error ?? "機器が接続されていません。"}</ViewMessage>;
    else if (!contract || !Form) content = <ViewMessage>この種類の機器の設定画面は未対応です。</ViewMessage>;
    else if (!settings) content = <ViewMessage>設定を取得できていません。</ViewMessage>;
    else content = <>
        {updatedElsewhere && <ViewMessage tone="warning">適用済み設定が別の操作で更新されました。編集中の項目は保持しています。</ViewMessage>}
        <section className="device-section">
            <h3>{contract.title} <span className="view-note" role="status">{progress.applying ? "適用中" : progress.pending ? "送信待ち" : draft ? "未反映" : "適用済み"}</span></h3>
            <Form settings={settings} inputs={{ ...contract.getInputs(settings), ...editValues(draft?.changes ?? {}) }} panelId={panelId}
                disabled={disabled} locked={locked} onChange={edit} onCommit={() => editor.current?.flush()} />
        </section>
        {validationError && <ViewMessage tone="error">{validationError}</ViewMessage>}
    </>;

    return <ViewLayout toolbar={<>
        <DeviceSelector panelId={panelId} devices={devices} value={selectedId} onChange={changeTarget} disabled={reading} />
        {selected && <DeviceStatus status={selected.status} />}
        {selected?.status === "connected" && contract && <button type="button" disabled={reading || writing || progress.pending}
            onClick={() => { void refreshSettings(); }}>設定を取得</button>}
    </>} actions={selected && contract && settings ? <>
        <OperationButton request={{ operation: "updateSettings", deviceId: selectedId, settings: candidate }}
            disabledReason={reading || progress.pending ? "設定を処理しています。" : manualError || (!changed ? "変更はありません。" : undefined)}
            onClick={() => apply("manual")}>設定を適用</OperationButton>
        {immediateChanged && <OperationButton request={{ operation: "updateSettings", deviceId: selectedId,
            settings: contract.merge(settings, editValues(modeEdits("immediate", true))) }}
            disabledReason={reading || progress.pending ? "設定を処理しています。" : !Object.keys(modeEdits("immediate", true)).length ? "入力を確認してください。" : undefined}
            onClick={() => apply("immediate")}>即時項目を再適用</OperationButton>}
        <button type="button" disabled={reading || !draft} title="未反映の入力を破棄" onClick={reset}>元に戻す</button>
    </> : undefined}>
        {selected && <DeviceDetails device={selected} />}
        {message && <ViewMessage tone={message.error ? "error" : "info"}>{message.text}</ViewMessage>}
        {content}
    </ViewLayout>;
}
