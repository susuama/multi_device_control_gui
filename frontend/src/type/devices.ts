// API通信の型を定義
//
// 機器の情報や状態、機器固有の設定
// backend側のapp/models/devices.pyと対応する

export interface DeviceInfo {
  id: string;             // デバイス固有のID
  type: string;           // デバイスタイプ （カメラ、パワーメータ...）
  manufacturer: string;   // メーカー名
  model: string;          // モデル名
  serial_number: string;  // シリアル番号。機器に登録された値
  address: string;        // デバイスのアドレス
  status: "connected" | "disconnected" | "error";         // デバイスの状態（オンライン、オフライン、エラー...）
}

// ------------デバイスタイプごとの型仕様 ------------

export interface PowerMeterSetting {
    // パワーメータ固有の属性
    wavelength: number;      // 波長 (nm)
    range: number;           // 測定範囲 (W)
    auto_range: boolean;     // 自動レンジ切替の有無
    bandwidth: string;       // 帯域幅 (low or high)
    averaging: number;       // (内部)平均化の点数
}

export interface CameraROI {
    x: number;
    y: number;
    width: number;
    height: number;
}

export interface CameraSetting {
    width: number;
    height: number;
    fps: number;
    exposure: number; // 秒。
    gain: number;
    gain_boost: boolean;
    pixel_format: "mono8" | "rgb24" | "bgr24";
    roi: CameraROI | null;
}

export type DeviceSettings = PowerMeterSetting | CameraSetting;

export interface SamplingStatus {
    is_sampling: boolean;
}
