// backend/app/models/windows.py と対応する API 通信の型。
export interface WindowInfo {
    id: string;
    title: string;
    position: [number, number];
    size: [number, number];
}

export interface CreateWindowRequest {
    title: string;
    path: string;
}
