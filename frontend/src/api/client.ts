import { logger } from "../logging/logStore.ts";

export type ApiRequestOptions = RequestInit & { timeoutMs?: number };

export class ApiError extends Error {
    readonly status: number | null;
    readonly outcomeUnknown: boolean;

    constructor(message: string, status: number | null, outcomeUnknown: boolean, cause?: unknown) {
        super(message, { cause });
        this.name = "ApiError";
        this.status = status;
        this.outcomeUnknown = outcomeUnknown;
    }
}

async function request<T>(path: string, options: ApiRequestOptions, consume: (response: Response) => Promise<T>): Promise<T> {
    const { timeoutMs = 10000, signal, ...fetchOptions } = options;
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new RangeError("timeoutMsは正の有限値で指定してください。");
    const headers = new Headers(options.headers);
    if (options.body != null && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    const method = (options.method ?? "GET").toUpperCase();
    const changesState = method !== "GET" && method !== "HEAD" && method !== "OPTIONS";
    const controller = new AbortController();
    let sent = false;
    let failureStatus: number | null = null;
    let responseStatus: number | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const abort = () => controller.abort(signal?.reason);
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) abort();
    const startedAt = performance.now();
    logger.info("api", `${method} ${path} を送信`);
    const cancelled = new Promise<never>((_resolve, reject) => {
        const rejectAbort = () => reject(controller.signal.reason);
        controller.signal.addEventListener("abort", rejectAbort, { once: true });
        if (controller.signal.aborted) rejectAbort();
        timer = setTimeout(() => controller.abort(new Error(`要求がタイムアウトしました (${timeoutMs} ms)。`)), timeoutMs);
    });
    try {
        if (controller.signal.aborted) return await cancelled;
        sent = true;
        const response = await Promise.race([fetch(path, { ...fetchOptions, headers, signal: controller.signal }), cancelled]);
        responseStatus = response.status;
        if (!response.ok) failureStatus = response.status;
        const message = `${method} ${path} → HTTP ${response.status} (${Math.round(performance.now() - startedAt)} ms)`;
        if (response.ok) logger.info("api", message);
        else logger.error("api", message);
        return await Promise.race([consume(response), cancelled]);
    } catch (cause) {
        if (cause instanceof ApiError) throw cause;
        if (cause instanceof SyntaxError) {
            if (!changesState || !sent) throw cause;
            throw new ApiError(`${method} ${path} の応答をJSONとして読み取れず、操作結果を確認できませんでした。`, responseStatus, true, cause);
        }
        const message = `${method} ${path} 通信失敗: ${cause instanceof Error ? cause.message : String(cause)}`;
        logger.error("api", message);
        throw new ApiError(message, failureStatus, changesState && sent && failureStatus === null, cause);
    } finally {
        clearTimeout(timer);
        signal?.removeEventListener("abort", abort);
    }
}

/** 応答本文を消費せず返す。timeoutMsは応答ヘッダー受信までの待機上限。 */
export function apiRawRequest(path: string, options: ApiRequestOptions = {}): Promise<Response> {
    return request(path, options, async (response) => response);
}

/** JSON本文の読み取りまで待機上限を適用する。変更要求は自動再送しない。 */
export function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
    return request(path, options, async (response) => {
        if (!response.ok) {
            const body: unknown = await response.json().catch(() => null);
            const detail = body !== null && typeof body === "object" && "detail" in body ? body.detail : undefined;
            const message = typeof detail === "string" ? detail : detail != null ? JSON.stringify(detail) : response.statusText;
            throw new ApiError(`HTTP ${response.status}: ${message}`, response.status, false);
        }
        try {
            return await response.json() as T;
        } catch (cause) {
            logger.error("api", `${path} の応答をJSONとして読み取れませんでした。`);
            throw cause;
        }
    });
}
