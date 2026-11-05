# WebSocketイベント配信API

`/ws` と `/ws/{device_id}` は、共通のEventBroadcasterからイベントを購読するAPI。
購読キーは配信先だけを指定し、送信形式はイベントの `type` で決める。

## 接続と購読

- `/ws` は `key=None`、`/ws/{device_id}` は `key=device_id` を購読する。
  `None` は全キー宛てではなく、`None` と完全一致する配信だけを受け取る。
- デバイスの存在・種別・接続状態は確認しない。未登録のキーも購読でき、配信を待機する。
- `maxsize` クエリ引数でQueueの保持数を指定する。省略時は100件、1以上の整数を受け付ける。
  不正な値は接続受付前に拒否する。
- 同じキーを複数の接続で購読した場合、接続ごとに独立したQueueを持つ。
- Queueが満杯の場合は、最古のイベントを破棄して新しいイベントを追加する。
  送信中のイベントや通信・受信側のバッファは、この件数に含まない。
- クライアントから届くメッセージは使用しない。

| 接続先の例 | 購読キー | Queueの上限 |
|---|---|---|
| `/ws` | `None` | 100件 |
| `/ws/meter-A` | `"meter-A"` | 100件 |
| `/ws/camera-A?maxsize=1` | `"camera-A"` | 1件 |

`maxsize` はイベントの種類によらず、その接続の全イベントへ適用する。
同じキーに数値と画像を配信し、上限を1件にすれば、数値も画像も最新の1件で置き換わる。

## イベントの種類と送信形式

- `type="camera_preview"` は `CameraPreviewEvent` で検証し、フレーム情報とJPEGをバイナリ送信する。
- `type="measurement"` は `MeasurementEvent` で検証し、JSON対応の辞書へ変換して送信する。
- 上記以外の `type` や、`type` がないイベントはエラーにする。検証に失敗したイベントは送信しない。
  エラー時も購読解除と送受信タスクの後処理を行う。
- どちらの形式も、`key=None` と文字列のキーの両方で配信できる。
- 同じ接続に両形式が届く場合、受信側はテキストとバイナリを判別して処理する。

配信元は購読者と同じキーを指定する。APIは配信先キーを補完・変換しない。
現状のMeasurementManagerは数値データを `key=None` へ配信するため、
個別キーでも受け取るには、配信元でそのキーへの `publish()` が必要。

## 画像イベントの作成

配信元は圧縮済みJPEGとフレーム情報から `CameraPreviewEvent` を作り、
Pythonの辞書を共通Broadcasterへ渡す。

```python
from backend.app.models.events import CameraPreviewEvent

event = CameraPreviewEvent(
    id=device_id,
    frame_index=frame_index,
    timestamp_ns=timestamp_ns,
    width=jpeg_width,
    height=jpeg_height,
    jpeg=jpeg_bytes,
)
event_broadcaster.publish(event.model_dump(), key=device_id)
```

`width`・`height` はJPEG画像の幅・高さ（画素数）。
`timestamp_ns` はPC取得時の単調増加カウンタで、Unix時刻や露光開始時刻ではない。
イベントの `id` は画像の取得元を示し、購読キーと独立して扱う。
たとえば、カメラAの画像をグループ用のキーや `None` へ配信することもできる。
APIはJPEGのデコード・検査・再圧縮を行わない。配信元で正しいJPEGを生成する。

## WebSocketメッセージの形式

1フレームを1件のバイナリメッセージにまとめる。

| 順序 | 内容 |
|---|---|
| 先頭4バイト | 続くJSON情報のバイト長。符号なし32 bit、ビッグエンディアン |
| 続く指定長のバイト列 | UTF-8のJSON情報 |
| 残りの全バイト | JPEG画像 |

JSON情報の例は次のとおり。

```json
{
  "type": "camera_preview",
  "id": "camera-A",
  "frame_index": 7,
  "timestamp_ns": "1000000000000000007",
  "width": 1280,
  "height": 1024
}
```

`timestamp_ns` はJavaScriptで整数精度を失わないよう、送信時に10進文字列へ変換する。
先頭の長さは文字数ではなく、UTF-8に符号化したJSONのバイト数。
JSON内にJPEGやBase64文字列は含めない。

## プレビューの生成

`main.py` で `CameraPreviewService` を1個生成し、MeasurementManagerと共有する。
配信先のEventBroadcasterは数値配信と同じインスタンスを使う。

```text
DummyCameraなどの取得ドライバー
  → MeasurementManagerの共通Queue・Processor
  → CameraPreviewService（元画像をコピーし、JPEGへ変換）
  → EventBroadcaster.publish(event, key=device_id)
  → /ws/{device_id}?maxsize=1
```

- カメラの取得開始時にプレビューの受付を開始する。設定通知が画像より先に届くことを前提とする。
- 変換待ちはカメラごとに最新1枚だけを保持する。変換中の1枚は別に保持する。
- JPEG変換は共通Processorと別の専用スレッドで行う。複数カメラでも変換スレッドは1本。
- 配信上限はカメラごとに10 fps、JPEG品質は80が既定値。
  `CameraPreviewService` の `max_fps`・`jpeg_quality` 引数で指定できる。
  変換完了から次の変換まで待機するため、実際のFPSは変換時間やカメラ台数によって下がる。
- `mono8`・`rgb24`・`bgr24` のC連続uint8画像を受け付け、BGRは色順を変換する。
- 元画像の解像度で配信し、保存ROIによる切り出し・縮小・回転・正規化は行わない。
- 保存処理には従来どおり元画像を渡す。プレビュー用の間引きで保存対象を減らさない。
- プレビューのコピー・変換に失敗しても、保存処理や数値配信は継続する。
  変換・配信エラーはログと `get_error(device_id)` で確認でき、次の正常処理で解除する。
- Processorでは画像と数値の配信側への受け渡しを分岐し、その後に保存処理を行う。
  数値イベントの生成・配信予約が失敗した場合も、エラーをログへ残して保存処理を続ける。
- カメラの取得中は購読者の有無によらずJPEGを生成する。
- JPEG変換には `requirements.txt` のPillowが必要。プレビューだけならFFmpegは不要。

## 終了と実装範囲

- クライアントの切断・送信失敗・タスクのキャンセル時は、購読を解除して送受信タスクを終了する。
- アプリ終了時のBroadcasterの終了通知では、WebSocket接続を閉じる。
- WebSocketの開閉では、デバイスの取得・記録を開始・停止しない。
- MeasurementManagerでカメラ取得を停止すると、変換待ち画像と変換中の結果を無効にする。
  再開始時には開始前のPC取得時刻を持つフレームも受け付けない。
  すでにBroadcasterや通信バッファへ渡した画像を取り消す処理は行わない。
- アプリ終了時はプレビューの受付を止め、変換スレッドを回収する。
  終了待機が上限を超えた場合は例外を返し、終了待機を再試行できる。
- デバイスの切断・取得停止の通知は、取得処理との連携時に実装する。
- ダミーカメラの取得からWebSocket送信までは実装済み。実カメラドライバーとフロントエンドの表示は未実装。
