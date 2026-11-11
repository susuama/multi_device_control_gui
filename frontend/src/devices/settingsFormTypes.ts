import type { SettingsInputs } from "./settingsValues";

export type SettingsFormProps<S, I extends SettingsInputs> = {
    settings: S; inputs: I; panelId: string; disabled: boolean; locked: readonly string[];
    onChange: <K extends keyof I & string>(name: K, value: I[K]) => void;
    onCommit: () => void;
};
