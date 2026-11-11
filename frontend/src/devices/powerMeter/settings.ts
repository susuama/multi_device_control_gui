import { booleanInput, checkInputNames, finite, numberInput } from "../settingsValues.ts";
import type { ApplyMode, InputValues, SettingsInputs } from "../settingsValues.ts";

export interface PowerMeterSetting {
    wavelength: number; // nm。
    range: number; // W。
    auto_range: boolean;
    bandwidth: string;
    averaging: number; // 機器内部の平均化点数。
}
export type PowerMeterInputs = InputValues<PowerMeterSetting>;
const modes = { wavelength: "manual", range: "manual", auto_range: "manual", bandwidth: "manual", averaging: "manual" } as const satisfies Record<keyof PowerMeterInputs, ApplyMode>;

function parse(value: unknown): PowerMeterSetting {
    if (!value || typeof value !== "object") throw new Error("パワーメーター設定の応答が不正です。");
    const v = value as Partial<PowerMeterSetting>;
    if (!finite(v.wavelength) || !finite(v.range) || !Number.isInteger(v.averaging)
        || typeof v.auto_range !== "boolean" || typeof v.bandwidth !== "string") throw new Error("パワーメーター設定の応答が不正です。");
    return value as PowerMeterSetting;
}

export const powerMeterSettings = {
    title: "測定設定", modes, parse,
    getInputs(value: unknown): PowerMeterInputs {
        const settings = parse(value);
        return { wavelength: String(settings.wavelength), range: String(settings.range), auto_range: settings.auto_range,
            bandwidth: settings.bandwidth, averaging: String(settings.averaging) };
    },
    merge(value: unknown, changes: SettingsInputs): PowerMeterSetting {
        const current = parse(value);
        checkInputNames(changes, modes);
        const bandwidth = changes.bandwidth ?? current.bandwidth;
        if (typeof bandwidth !== "string" || !bandwidth.trim()) throw new Error("帯域幅を選択してください。");
        return { ...current,
            wavelength: numberInput(changes, "wavelength", current.wavelength, "波長"), range: numberInput(changes, "range", current.range, "測定レンジ"),
            auto_range: booleanInput(changes, "auto_range", current.auto_range), bandwidth,
            averaging: numberInput(changes, "averaging", current.averaging, "内部平均化の点数", true),
        };
    },
};
