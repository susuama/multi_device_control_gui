import type { ComponentType } from "react";
import { settingsContracts } from "./settingsContracts";
import type { DeviceSettings, SettingsContract, SettingsType } from "./settingsContracts";
import type { SettingsFormProps } from "./settingsFormTypes";
import type { SettingsInputs } from "./settingsValues";
import { CameraSettingsForm } from "./camera/CameraSettingsForm";
import { PowerMeterSettingsForm } from "./powerMeter/PowerMeterSettingsForm";

type CommonFormProps = SettingsFormProps<DeviceSettings, SettingsInputs>;

function registerForm<S extends object, I extends SettingsInputs>(contract: SettingsContract<S> & { getInputs: (value: unknown) => I }, Form: ComponentType<SettingsFormProps<NoInfer<S>, NoInfer<I>>>) {
    return function RegisteredSettingsForm(props: CommonFormProps) {
        const settings = contract.parse(props.settings);
        const inputs = { ...contract.getInputs(settings), ...props.inputs } as I;
        return <Form {...props} settings={settings} inputs={inputs} />;
    };
}

export const settingsForms = {
    camera: registerForm(settingsContracts.camera, CameraSettingsForm),
    power_meter: registerForm(settingsContracts.power_meter, PowerMeterSettingsForm),
} satisfies Record<SettingsType, ComponentType<CommonFormProps>>;

