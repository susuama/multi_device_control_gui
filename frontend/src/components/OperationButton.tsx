import { useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { Button } from "@astryxdesign/core/Button";
import type { ButtonVariant } from "@astryxdesign/core/Button";
import { backendStore } from "../state/backendStore";
import { getOperationAvailability } from "../state/operationPolicy";
import type { OperationRequest } from "../state/operationPolicy";

/** 操作可否を共通判定し、無効な理由はツールチップと支援技術向け説明に渡す。 */
export function OperationButton({ request, onClick, children, disabledReason, label, variant = "secondary", hideReason = false }: {
    request: OperationRequest; onClick: () => void; children: ReactNode; disabledReason?: string; label?: string;
    variant?: ButtonVariant; hideReason?: boolean;
}) {
    const backend = useSyncExternalStore(backendStore.subscribe, backendStore.getSnapshot);
    const availability = getOperationAvailability(backend, request);
    const reason = disabledReason ?? (hideReason ? null : availability.reason);
    return (
        <Button
            className="ui-button"
            label={label ?? (typeof children === "string" ? children : "操作")}
            size="sm"
            variant={variant}
            isDisabled={!availability.allowed || !!disabledReason}
            tooltip={reason ?? undefined}
            aria-description={reason ?? undefined}
            onClick={onClick}
        >
            {children}
        </Button>
    );
}
