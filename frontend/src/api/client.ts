// Vite の /api プロキシを使い、同じホストにリクエストする。
export function apiRawRequest(
    path: string,
    options: RequestInit = {},
): Promise<Response> {
    const headers = new Headers(options.headers);
    if (options.body != null && !headers.has("Content-Type")) {
        headers.set("Content-Type", "application/json");
    }

    return fetch(path, { ...options, headers });
}

export async function apiRequest<T>(
    path: string,
    options: RequestInit = {},
): Promise<T> {
    const response = await apiRawRequest(path, options);

    if (!response.ok) {
        const body: unknown = await response.json().catch(() => null);
        const detail = body !== null && typeof body === "object" && "detail" in body
            ? body.detail
            : undefined;
        const message = typeof detail === "string"
            ? detail
            : detail != null
                ? JSON.stringify(detail)
                : response.statusText;

        throw new Error(`HTTP ${response.status}: ${message}`);
    }

    return response.json() as Promise<T>;
}
