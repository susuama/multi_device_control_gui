import { ApplyModeBadge, BooleanSetting, NumberSetting } from "../SettingsField";
import type { SettingsFormProps } from "../settingsFormTypes";
import { powerMeterSettings } from "./settings";
import type { PowerMeterInputs, PowerMeterSetting } from "./settings";

export function PowerMeterSettingsForm({ inputs, panelId, disabled, locked, onChange, onCommit }: SettingsFormProps<PowerMeterSetting, PowerMeterInputs>) {
    function numberField(name: "wavelength" | "range" | "averaging", label: string, unit: string, integer = false) {
        return <NumberSetting id={`${panelId}-${name}`} label={label} unit={unit} integer={integer} value={inputs[name]}
            mode={powerMeterSettings.modes[name]} disabled={disabled || locked.includes(name)}
            onChange={(value) => onChange(name, value)} onCommit={onCommit} />;
    }
    return <fieldset className="device-setting-fields" disabled={disabled}>
        <legend className="visually-hidden">測定設定</legend>
        {numberField("wavelength", "波長", "nm")}{numberField("range", "測定レンジ", "W")}
        <BooleanSetting id={`${panelId}-auto_range`} label="自動レンジ" value={inputs.auto_range}
            mode={powerMeterSettings.modes.auto_range} disabled={disabled || locked.includes("auto_range")}
            onChange={(value) => onChange("auto_range", value)} onCommit={onCommit} />
        <label className="device-setting-row" htmlFor={`${panelId}-bandwidth`}><span>帯域幅 <ApplyModeBadge mode={powerMeterSettings.modes.bandwidth} /></span>
            <select id={`${panelId}-bandwidth`} value={inputs.bandwidth} disabled={disabled || locked.includes("bandwidth")}
                onChange={(event) => onChange("bandwidth", event.target.value)} onBlur={onCommit}>
                {!["low", "high"].includes(inputs.bandwidth) && <option value={inputs.bandwidth}>{inputs.bandwidth}</option>}
                <option value="low">低（low）</option><option value="high">高（high）</option>
            </select>
        </label>
        {numberField("averaging", "内部平均化", "点", true)}
    </fieldset>;
}
