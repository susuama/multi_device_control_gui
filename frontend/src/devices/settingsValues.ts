export type SettingsInputs = Record<string, string | boolean>;
export type InputValues<S> = { [K in keyof S]: S[K] extends boolean ? boolean : string };
export type ApplyMode = "immediate" | "manual";

export function numberInput(inputs: SettingsInputs, name: string, fallback: number, label: string, integer = false, allowZero = false): number {
    const raw = inputs[name];
    if (raw === undefined) return fallback;
    const value = typeof raw === "string" && raw.trim() !== "" ? Number(raw) : NaN;
    if (!Number.isFinite(value) || (allowZero ? value < 0 : value <= 0) || (integer && !Number.isSafeInteger(value))) {
        throw new Error(`${label}は${allowZero ? "0以上" : "0より大きい"}${integer ? "整数" : "数値"}で入力してください。`);
    }
    return value;
}

export function booleanInput(inputs: SettingsInputs, name: string, fallback: boolean): boolean {
    const raw = inputs[name];
    if (raw === undefined) return fallback;
    if (typeof raw !== "boolean") throw new Error("チェック項目の値が不正です。");
    return raw;
}

export function checkInputNames(changes: SettingsInputs, modes: Readonly<Record<string, ApplyMode>>) {
    for (const name of Object.keys(changes)) {
        if (!Object.hasOwn(modes, name)) throw new Error(`未対応の設定項目です: ${name}`);
    }
}

export function settingsEqual(left: object, right: object): boolean {
    return Object.keys(left).length === Object.keys(right).length && Object.entries(left)
        .every(([key, value]) => JSON.stringify(value) === JSON.stringify(Reflect.get(right, key)));
}

export const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
