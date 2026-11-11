import type { ApplyMode } from "./settingsValues";

type FieldProps = { id: string; label: string; mode: ApplyMode; disabled: boolean; onCommit: () => void };
export function ApplyModeBadge({ mode }: { mode: ApplyMode }) {
    return <small className="setting-mode">{mode === "immediate" ? "即時" : "ボタン"}</small>;
}
export function NumberSetting({ id, label, mode, disabled, onCommit, value, onChange, unit = "", integer = false }:
    FieldProps & { value: string; onChange: (value: string) => void; unit?: string; integer?: boolean }) {
    return <label className="device-setting-row" htmlFor={id}>
        <span>{label} <ApplyModeBadge mode={mode} /></span>
        <span className="device-unit-input"><input id={id} type="number" step={integer ? "1" : "any"} value={value}
            disabled={disabled} onChange={(event) => onChange(event.target.value)} onBlur={onCommit}
            onPointerUp={onCommit} onKeyUp={(event) => { if (["Enter", "ArrowUp", "ArrowDown"].includes(event.key)) onCommit(); }} />
            {unit && <span>{unit}</span>}
        </span>
    </label>;
}
export function BooleanSetting({ id, label, mode, disabled, onCommit, value, onChange }:
    FieldProps & { value: boolean; onChange: (value: boolean) => void }) {
    return <label className="device-setting-row" htmlFor={id}>
        <span>{label} <ApplyModeBadge mode={mode} /></span>
        <span><input id={id} type="checkbox" checked={value} disabled={disabled}
            onChange={(event) => onChange(event.target.checked)} onBlur={onCommit} onPointerUp={onCommit}
            onKeyUp={(event) => { if (["Enter", " "].includes(event.key)) onCommit(); }} /> 有効</span>
    </label>;
}
