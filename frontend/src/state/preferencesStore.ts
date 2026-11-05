type Preferences = Readonly<{ theme: "dark" | "light"; defaultDirectory: string; error: string }>;
type PreferenceStorage = Pick<Storage, "getItem" | "setItem">;
export const PREFERENCES_STORAGE_KEY = "multi-device-control:preferences:v1";

/** 保存先が使用できなくても、現在のウィンドウ内で設定を保持する。 */
export function createPreferencesStore(storage?: PreferenceStorage) {
    let state: Preferences = { theme: "dark", defaultDirectory: "", error: "" };
    const listeners = new Set<() => void>();
    let target = storage;
    try {
        if (target === undefined && typeof window !== "undefined") target = window.localStorage;
        const saved = target?.getItem(PREFERENCES_STORAGE_KEY);
        if (saved !== undefined && saved !== null) {
            const value: unknown = JSON.parse(saved);
            if (typeof value !== "object" || value === null || !("theme" in value)
                || (value.theme !== "dark" && value.theme !== "light")
                || !("defaultDirectory" in value) || typeof value.defaultDirectory !== "string") {
                throw new Error("保存された設定の形式が正しくありません。");
            }
            state = { theme: value.theme, defaultDirectory: value.defaultDirectory, error: "" };
        }
    } catch (cause) {
        state = { ...state, error: `設定を読み込めませんでした: ${cause instanceof Error ? cause.message : String(cause)}` };
    }
    return {
        getSnapshot: () => state,
        subscribe(listener: () => void) {
            listeners.add(listener);
            return () => { listeners.delete(listener); };
        },
        update(changes: Partial<Pick<Preferences, "theme" | "defaultDirectory">>) {
            const next = { theme: changes.theme ?? state.theme, defaultDirectory: changes.defaultDirectory ?? state.defaultDirectory };
            let error = state.error;
            if (target) {
                try {
                    target.setItem(PREFERENCES_STORAGE_KEY, JSON.stringify(next));
                    error = "";
                } catch (cause) {
                    error = `設定を保存できませんでした: ${cause instanceof Error ? cause.message : String(cause)}`;
                }
            }
            if (next.theme === state.theme && next.defaultDirectory === state.defaultDirectory && error === state.error) return;
            state = { ...next, error };
            listeners.forEach((listener) => listener());
        },
    };
}

export const preferencesStore = createPreferencesStore();
