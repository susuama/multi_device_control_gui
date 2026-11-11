import { BooleanSetting, NumberSetting } from "../SettingsField";
import type { SettingsFormProps } from "../settingsFormTypes";
import { cameraSettings } from "./settings";
import type { CameraInputs, CameraSetting } from "./settings";

export function CameraSettingsForm({ settings, inputs, panelId, disabled, locked, onChange, onCommit }: SettingsFormProps<CameraSetting, CameraInputs>) {
    function numberField(name: "exposure" | "gain" | "fps", label: string, unit = "") {
        return <NumberSetting id={`${panelId}-${name}`} label={label} unit={unit} value={inputs[name]}
            mode={cameraSettings.modes[name]} disabled={disabled || locked.includes(name)}
            onChange={(value) => onChange(name, value)} onCommit={onCommit} />;
    }
    return <>
        <fieldset className="device-setting-fields" disabled={disabled}>
            <legend className="visually-hidden">撮像設定</legend>
            {numberField("exposure", "露光時間", "ms")}{numberField("gain", "ゲイン")}
            <BooleanSetting id={`${panelId}-gain_boost`} label="ゲインブースト" value={inputs.gain_boost}
                mode={cameraSettings.modes.gain_boost} disabled={disabled || locked.includes("gain_boost")}
                onChange={(value) => onChange("gain_boost", value)} onCommit={onCommit} />
            {numberField("fps", "撮影FPS", "fps")}
        </fieldset>
        <section className="device-section"><h3>画像・保存ROI（現在値）</h3>
            <dl className="device-details-list"><dt>画像サイズ</dt><dd>{settings.width} × {settings.height} px</dd>
                <dt>画素形式</dt><dd>{settings.pixel_format}</dd><dt>保存ROI</dt>
                <dd>{settings.roi ? `x=${settings.roi.x}, y=${settings.roi.y}, 幅=${settings.roi.width}, 高さ=${settings.roi.height} px` : "全画面"}</dd>
            </dl>
        </section>
    </>;
}
