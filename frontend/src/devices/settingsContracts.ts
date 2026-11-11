import { cameraSettings } from "./camera/settings.ts";
import { powerMeterSettings } from "./powerMeter/settings.ts";
import type { ApplyMode, SettingsInputs } from "./settingsValues.ts";

export interface SettingsContract<S extends object = object> {
    title: string;
    modes: Readonly<Record<string, ApplyMode>>;
    parse: (value: unknown) => S;
    getInputs: (value: unknown) => SettingsInputs;
    merge: (value: unknown, changes: SettingsInputs) => S;
}

export const settingsContracts = {
    camera: cameraSettings, power_meter: powerMeterSettings,
} satisfies Record<string, SettingsContract>;
export type SettingsType = keyof typeof settingsContracts;
export type DeviceSettings = ReturnType<(typeof settingsContracts)[SettingsType]["parse"]>;

export function getSettingsContract(type: string): SettingsContract<DeviceSettings> | undefined {
    return Object.hasOwn(settingsContracts, type) ? settingsContracts[type as SettingsType] : undefined;
}

export function validateDeviceSettings(value: unknown, type: string): void {
    const contract = getSettingsContract(type);
    if (!contract) throw new Error(`未対応の機器種類です: ${type}`);
    contract.parse(value);
}
