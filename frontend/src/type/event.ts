// イベント通知の型
//
// WabSocket通信で送受信するイベント通知の型を定義
// backend側のapp/models/event.pyと対応する

export interface EventBase {
  type: string;         // イベントの種類
  source: string;       // イベントの発生源（デバイスIDやシステムなど）
  sequence: number;     // イベントのシーケンス番号
  time: string;         // イベントの発生時刻
}

// -----------イベントの種類ごとの型仕様 ------------


export interface PowerSample {
    power: number;        // 光パワー (W)
}

