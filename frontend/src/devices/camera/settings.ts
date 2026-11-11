import { booleanInput, checkInputNames, finite, numberInput } from "../settingsValues.ts";
import type { ApplyMode, InputValues, SettingsInputs } from "../settingsValues.ts";

export interface CameraROI { x: number; y: number; width: number; height: number }
export interface CameraSetting {
    width: number; height: number; fps: number;
    exposure: number; // 秒。
    gain: number; gain_boost: boolean;
    pixel_format: "mono8" | "rgb24" | "bgr24";
    roi: CameraROI | null;
}
export type CameraInputs = InputValues<Pick<CameraSetting, "exposure" | "gain" | "gain_boost" | "fps">>;
const modes = { exposure: "immediate", gain: "immediate", gain_boost: "manual", fps: "manual" } as const satisfies Record<keyof CameraInputs, ApplyMode>;

function parse(value: unknown): CameraSetting {
    if (!value || typeof value !== "object") throw new Error("カメラ設定の応答が不正です。");
    const v = value as Partial<CameraSetting>;
    if (!finite(v.width) || !Number.isInteger(v.width) || v.width <= 0 || !finite(v.height) || !Number.isInteger(v.height) || v.height <= 0
        || !finite(v.fps) || v.fps <= 0 || !finite(v.exposure) || v.exposure <= 0 || !finite(v.gain) || v.gain < 0
        || typeof v.gain_boost !== "boolean" || !["mono8", "rgb24", "bgr24"].includes(v.pixel_format ?? "")
        || !(v.roi === null || v.roi && [v.roi.x, v.roi.y, v.roi.width, v.roi.height].every(Number.isInteger)
            && v.roi.x >= 0 && v.roi.y >= 0 && v.roi.width > 0 && v.roi.height > 0
            && v.roi.x + v.roi.width <= v.width && v.roi.y + v.roi.height <= v.height)) {
        throw new Error("カメラ設定の応答が不正です。");
    }
    return value as CameraSetting;
}

export const cameraSettings = {
    title: "撮像設定", modes, parse,
    getInputs(value: unknown): CameraInputs {
        const settings = parse(value);
        return { exposure: String(settings.exposure * 1000), gain: String(settings.gain), gain_boost: settings.gain_boost, fps: String(settings.fps) };
    },
    merge(value: unknown, changes: SettingsInputs): CameraSetting {
        const current = parse(value);
        checkInputNames(changes, modes);
        return { ...current,
            exposure: changes.exposure === undefined ? current.exposure : numberInput(changes, "exposure", 0, "露光時間") / 1000,
            gain: numberInput(changes, "gain", current.gain, "ゲイン", false, true),
            gain_boost: booleanInput(changes, "gain_boost", current.gain_boost), fps: numberInput(changes, "fps", current.fps, "FPS"),
        };
    },
};
