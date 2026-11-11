// API通信の型を定義
//
// 機器の情報や状態、機器固有の設定
// backend側のapp/models/devices.pyと対応する

export interface DiscoveredDevice {
  driver: string;
  type: string;
  manufacturer: string | null;
  model: string;
  serial_number: string | null;
  address: string;
}

export interface DeviceInfo {
  error?: string | null;
  id: string;             // デバイス固有のID
  type: string;           // デバイスタイプ （カメラ、パワーメータ...）
  manufacturer: string;   // メーカー名
  model: string;          // モデル名
  serial_number: string;  // シリアル番号。機器に登録された値
  address: string;        // デバイスのアドレス
  status: "connected" | "disconnected" | "error";         // デバイスの状態（オンライン、オフライン、エラー...）
}

export type { DeviceSettings } from "../devices/settingsContracts.ts";
export type { CameraROI, CameraSetting } from "../devices/camera/settings.ts";
export type { PowerMeterSetting } from "../devices/powerMeter/settings.ts";

export interface SamplingStatus {
    is_sampling: boolean;
}
