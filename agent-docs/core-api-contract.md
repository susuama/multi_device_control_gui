# フロントエンド中核実装の前提APIと担当範囲

作成日：2026-11-02

2026-11-08確認：以下の「要求仕様」「不足API」「担当取消」は作成当時の経緯を含む。現在のbackend/app/main.pyは測定状態・記録・イベントAPIを登録済みで、通常の測定・記録ビューも同じ契約を利用する。終了時の最終JSON保存失敗では両状態フラグがfalseでも停止再試行が必要になる場合がある。再試行成功後も過去のerrorは保持されるため、errorの有無から未解放リソースや保存完了を推測しない。

状態：2026-11-02の最新指示により、サブエージェントのバックエンド変更は取り消す。フロントエンドを本書の将来API契約に合わせて先行実装し、不足APIは後から実装する。

本書のバックエンド部分は要求仕様であり、現在のAPIが対応済みであることを意味しない。フロントエンドは契約に対応したモックで検証し、現行APIの不足はunsupportedとして明示する。IDや対象情報をフロント側で捏造しない。

## 1. 完成条件と既存契約

画面を作らずに、共通層の操作で初期化、機器操作、数値・画像受信、記録、状態追従、通信復旧、受信終了を検証できることを中核の完成条件とする。

次の既存契約を維持する。

- 実際の機器操作、取得、保存はバックエンドが担当する。
- 取得と記録は独立する。記録停止後も取得・プレビューを継続できる。
- 現在の記録開始APIは、開始時に取得中の全機器を対象とする。本書では取得と独立した対象選択APIを追加しない。
- 記録開始時の対象構成、保存形式、保存ROIを固定する。露光・ゲインの変更は既存の許可条件を維持する。
- `/ws` はキーなし、`/ws/{device_id}` は指定キーの完全一致購読。イベント種別はキーから決めず、`type` で決める。
- 画像の送信形式は4バイトのビッグエンディアンJSON長、UTF-8 JSON、JPEGの順とする。
- パネルや表示購読の終了では、機器の取得・記録を停止しない。
- ブラウザーの終了時に、非同期の停止APIが完了することを前提にしない。アプリ全体の機器終了処理は既存バックエンドのライフサイクルが担当する。

確認元：`backend/app/models/recordings.py`、`backend/app/models/events.py`、`backend/app/services/measurement_manager.py`、`backend/app/services/data_racorder.py`、`backend/app/api/events.py`。

## 2. 識別IDの定義

| 項目 | 意味・有効期間 |
|---|---|
| `server_instance_id` | バックエンドのサービス群の起動ごとに新しく発行する不透明な文字列。同じ起動中はHTTP接続・WebSocket接続が変わっても同じ値。サーバー再起動後は別の値とする。認証情報には使わない。 |
| `measurement_id` | 共通の測定開始基準に対応する不透明な文字列。共通の基準時刻が新規作成・更新される際に新しく発行する。別機器の取得追加や、同じ基準を維持する個別の停止・再開では変更しない。 |
| `recording_id` | 1回の記録コンテキストに対応する不透明な文字列。保存先と開始時対象を持つコンテキストの生成時に発行し、停止後も次のコンテキスト生成まで保持する。 |

フロントエンドはIDの形式や大小関係を解釈せず、等しいかどうかだけを比較する。測定を識別するキーには `(server_instance_id, measurement_id)` を使う。

`measurement_id` は最初の取得開始が成功するまでは `null` とし、最後の取得を停止した後も直近のIDと開始日時を保持する。次の取得開始時に既存実装が共通の基準時刻を維持する場合は、同じIDを維持する。取得開始の失敗で、存在しない測定を現在の測定として公開しない。

記録開始要求がコンテキスト生成前の検証で失敗した場合、新しい `recording_id` を発行しない。コンテキスト生成後に一部の保存初期化が失敗した場合は、そのIDと対象を保持し、失敗・終了処理の状態を取得できるようにする。

## 3. 新規 GET /api/measurements/status

現在の共通測定基準と取得中IDを返す。GETによって探索・接続・取得・記録を開始しない。

```json
{
  "server_instance_id": "server-A",
  "measurement_id": "measurement-A",
  "started_at": "2026-11-02T10:00:00+09:00",
  "sampling_device_ids": ["camera-A", "meter-A"],
  "error": null
}
```

| 項目 | 型 | 意味 |
|---|---|---|
| `server_instance_id` | string | 必須。第2節の定義に従う。 |
| `measurement_id` | string または null | 初回取得前はnull。 |
| `started_at` | ISO 8601日時文字列 または null | 共通測定基準の日時。タイムゾーン付き。IDがnullなら日時もnull。 |
| `sampling_device_ids` | string[] | 応答時点で取得中のデバイスID。全停止時は空配列。記録対象一覧とは別に扱う。 |
| `error` | string または null | 共通取得処理が保持するエラー理由。Queue異常などで取得を停止した場合も取得できる。解除条件は既存の取得エラー管理に合わせる。 |

この応答内のID・開始日時・取得中IDは、互いに矛盾しない状態として読み出す。複数の既存APIの応答が、同時刻の状態になることまでは要求しない。

`GET /api/devices` と `GET /api/devices/{id}/settings` は既存のものを使う。既存の個別取得状態APIも残す。

## 4. 記録状態レスポンスの拡張

対象は `GET /api/recordings/status` と、`POST /api/recordings/start`・`POST /api/recordings/stop` の成功応答。同じ拡張済みの状態モデルを返す。

```json
{
  "server_instance_id": "server-A",
  "recording_id": "recording-A",
  "measurement_id": "measurement-A",
  "is_recording": true,
  "is_stopping": false,
  "directory": "C:/data/run-A",
  "name": "run-A",
  "error": null,
  "targets": {
    "camera-A": {
      "type": "camera",
      "settings_at_start": {
        "width": 640,
        "height": 480,
        "fps": 10,
        "exposure": 0.01,
        "gain": 1,
        "gain_boost": false,
        "pixel_format": "mono8",
        "roi": null
      },
      "video_options": { "format": "ffv1" },
      "state": "recording",
      "error": null,
      "acquisition_error": null,
      "locked_settings": ["fps", "roi"]
    }
  }
}
```

既存の5項目 `is_recording`・`is_stopping`・`directory`・`name`・`error` の型と意味は維持する。記録が一部継続している状態とエラーは併存できる。

追加項目の定義：

- `server_instance_id`：現在のバックエンド起動ID。測定状態APIと同じ値。
- `recording_id`：記録コンテキスト未生成ならnull。それ以外は第2節のID。
- `measurement_id`：その記録を開始したときの測定ID。記録未作成ならnull。現在の測定状態APIのIDへ後から置き換えない。
- `targets`：記録開始時に確定したIDをキーにする辞書。初回記録前は空辞書。記録中の取得追加ではキーを追加しない。個別停止や異常で対象を削除せず、次の記録コンテキストまで保持する。
- `targets[id].type`：開始時の機器種別。
- `targets[id].settings_at_start`：開始時に確定した適用済み設定のコピー。設定変更で書き換えない。現在の設定は既存の設定APIから取得する。
- `targets[id].video_options`：開始時の動画保存形式。カメラ以外はnull。
- `targets[id].state`：下表の保存側の状態。
- `targets[id].error`：当該対象の保存処理が保持する失敗理由。失敗でなければnull。
- `targets[id].acquisition_error`：その記録中に発生した機器の取得失敗理由。取得失敗がなければnull。機器の切断・再接続後も、その記録の履歴として保持する。保存状態のstate・errorとは独立する。
- `targets[id].locked_settings`：当該対象で、既存の設定更新APIが現在変更を拒否する設定名の配列。現行の制限対象カメラでは `["fps", "roi"]`、制限がなければ空配列。記録状態の見た目から推測せず、実際の検証条件と一致させる。

| `state` | 意味 |
|---|---|
| `not_started` | 対象には確定したが、保存処理の準備が完了していない。開始途中の失敗時にも使う。 |
| `recording` | 保存データを受け付ける状態。取得中であること自体を意味しない。 |
| `stopping` | 新規受付を止め、残りの書き込みやリソースの終了を処理している。 |
| `completed` | データを保存し、正常に終了した。 |
| `empty` | データを保存せず、正常に終了した。 |
| `failed` | 保存処理で失敗した。失敗理由をerrorへ格納する。リソースの後処理完了は記録全体のis_stoppingでも確認する。 |

状態は保存処理の実際の状態を返す。取得停止だけで `completed` にしない。CSVや動画の保存オブジェクトを閉じて内部の管理辞書から外しても、対象情報と最終結果は残す。

現行の `is_recording_device()` は記録全体の受付状態と開始時の対象IDで判定するため、個別の保存状態と設定ロックが一致するとは限らない。`locked_settings` の追加でこの違いを表す。設定ロックの範囲を変更する仕様ではない。

記録の終了処理中もGETを受け付ける。停止APIの終了待機と同じロックを保持して、状態取得まで終了待ちにしない。

## 5. 数値・画像イベントの拡張

`measurement` のJSONと、`camera_preview` のJSONメタデータに、次の必須項目を追加する。

```json
{
  "server_instance_id": "server-A",
  "measurement_id": "measurement-A"
}
```

- 配信する測定データのIDはnullにしない。
- どちらのイベントも、実際にそのデータを取得した測定期間のIDを付ける。
- Queue待ちやJPEG変換待ちの間に取得停止・再開始があっても、古いデータへ現在のIDを付け直さない。古いデータは元のIDを保持するか、既存の停止処理で破棄する。
- 遅いWebSocket送信が新しい測定開始後に完了しても、元のIDを保持する。
- 購読キーと取得元の `id` は引き続き独立させる。
- 画像のJSON長は、追加項目を含むUTF-8のバイト長から計算する。JPEG本体とフレーミングは変更しない。
- 画像の `timestamp_ns` は従来どおり10進文字列。数値イベントの既存項目の型は変更せず、フロント側の時間軸には `elapsed_s` を使う。

フロント側は異なるサーバー・測定期間のデータを同じ履歴へ混在させない。状態取得と受信が前後した場合は、IDが異なるという理由だけで受信データを現在の測定として採用せず、共通層が状態を再取得して確認する。

## 6. 今回追加しないバックエンド機能

- 取得とは独立した記録対象選択、機器別保存先、独立した複数の記録セッション。
- 過去データの再送、保存データの閲覧、切断中に失われた数値の復元。
- 新しい状態変更イベント。初期のフロント実装は既存APIと追加の状態APIを定期取得する。
- ブラウザーからのアプリ全体の強制終了API。
- 実カメラドライバー、カメラ設定項目の追加、コーデック既定値の確定。
- 書き込みAPIの自動再実行。タイムアウトは処理未実行の証明にならないため、フロント側は状態を再確認し、接続復帰だけを理由に取得・記録を再開始しない。

これは必要な読出し情報と識別情報を補う契約であり、取得・記録の対象や停止・保存の仕様を変更するものではない。

## 7. バックエンドの受け入れ条件

将来のバックエンド実装を接続する際に、次を確認する。今回のフロントエンド実装は先行してモックで確認する。

1. 初回取得前の測定状態、初回記録前の記録状態を、副作用なく取得できる。
2. HTTP再接続・WebSocket再接続では起動ID・測定IDが変わらず、サーバー再起動では起動IDが変わる。
3. 複数機器の追加取得は共通測定IDを共有する。共通の基準時刻を更新する次の測定では別IDになる。取得開始失敗のIDを現在値として残さない。
4. Queue待ち・JPEG変換待ち・送信待ちを挟んでも、データに対応する測定IDが保たれる。
5. 記録対象の開始時設定・保存形式が不変で、現在の適用済み設定と区別できる。
6. 取得途中の追加機器が記録対象へ混入せず、個別終了・失敗した対象と最終結果を停止後も取得できる。
7. FPS・ROIの設定更新が拒否される条件とlocked_settingsが一致する。露光・ゲインの既存の許可条件を維持する。
8. 保存継続と個別エラーの併存、終了処理中、正常終了、空の記録、開始途中の失敗を状態として区別できる。
9. 停止APIが保存完了を待つ間も、状態GETが応答する。
10. 既存のAPI項目・数値配信・画像バイナリ形式を維持し、関連するバックエンドテストを通す。

## 8. API準備後のフロントエンド実装方針

単一ウィンドウの中核を完成範囲とし、製品用の各ビューやレイアウト拡張は含めない。

- 起動時の状態取得を共通層が行い、未初期化・初期化中・利用可能・通信異常・終了を明示する。
- 状態取得をウィンドウ単位でまとめ、パネル数に依存せず定期更新する。処理中の周期を重ねず、古い応答は反映しない。
- 通信断では最後の確認値と未確認状態を区別する。接続復帰時に状態を再取得し、バックエンドの実際の取得・記録を尊重する。
- 必要な受信接続を共通層で管理する。取得・保存の開始停止と表示購読の開始終了を混同しない。
- HTTP書き込みのタイムアウト時は結果不明として扱う。自動で同じ操作を再送しない。
- 数値履歴・画像を測定IDごとに扱い、通信断や受信停止・更新時刻を表示側へ提供する。切断中の欠落を補完済みと扱わない。
- 操作の可否と理由を共通の操作判定から取得できるようにする。最終的な検証はバックエンドに残す。
- アプリ設定は最小限の配色・標準保存先を共有・保存する。既定のコーデックを新しく決めない。
- 共通層の終了は定期取得・再接続待機・受信接続を回収する。機器の停止は明示操作とバックエンドの終了処理に従う。
- バックエンドとの契約が不足・不一致なら、対応済みとして処理せず、初期化エラーとして通知する。

## 9. 担当ファイルの分割案

### 先行するバックエンドの担当

以下のバックエンド分担は取消済み。担当の変更とメインが追加したバックエンド契約テストを取り消した。ファイル一覧は作業履歴として残す。

- 測定・配信担当：`backend/app/main.py`、新規 `backend/app/api/measurements.py`、`backend/app/models/measurement.py`、`backend/app/models/events.py`、`backend/app/services/measurement_manager.py`、`backend/app/services/camera_preview.py`。テストは `test_measurement.py`、`test_measurement_api.py`、新規 `test_measurement_status.py`、`test_event_models.py`、`test_events_api.py`、`test_camera_preview.py`、`test_metadata.py`、`test_device_data.py`、`test_queue_overflow.py`、`test_device_disconnect.py`、`test_dummy_camera.py`、`test_event_broadcaster.py`（すべて `backend/tests/` 配下）。
- 記録状態担当：`backend/app/models/recordings.py`、`backend/app/services/data_racorder.py`、`backend/app/api/recordings.py`、`backend/tests/test_data_recorder.py`、`backend/tests/test_video_recorder.py`、`backend/tests/test_camera_recording.py`、`backend/tests/test_recording_lifecycle.py`、新規 `backend/tests/test_recording_status.py`。
- メイン：新規 `backend/tests/test_core_api_contract.py` と本書。担当間の修正は所有者へ差し戻す。

内部の接続契約は `DataRecorder(server_instance_id=...)`、`DataRecorder.start(..., targets=..., measurement_id=...)` とする。単独利用の既存テストを維持するため、生成時のIDは省略時に一度だけ発行し、startの測定IDは省略可能とする。実際のHTTP経路ではMeasurementManagerが起動IDと測定IDを渡す。RecordingCommandは開始要求時の測定IDを保持し、Processorへ渡す。

### 後続のフロントエンドの担当

サブエージェントは担当ファイル以外の変更・作成・削除を禁止する。フロントエンド担当はバックエンドを変更しない。依存関係ファイル、共通のプロジェクト指示を変更しない。担当範囲外の修正が必要なら、書き換えずにメインエージェントへ報告する。Gitのcommit・reset・checkout・cleanは行わない。

| 担当 | 所有するファイル | 責務 |
|---|---|---|
| A：通信 | `frontend/src/api/client.ts`、`frontend/src/api/websocketClient.ts`、`frontend/src/api/eventDecoder.ts`、`frontend/tests/apiClient.test.mjs`、`frontend/tests/websocketClient.test.mjs`、新規 `frontend/tests/eventDecoder.test.mjs` | HTTP待機の制御、WebSocketの接続共有・復旧・解放、イベント解析。機器の取得・記録操作を自動再送しない。 |
| B：状態と操作 | `frontend/src/api/devices.ts`、`frontend/src/api/recordings.ts`、新規 `frontend/src/api/measurements.ts`、`frontend/src/type/devices.ts`、`frontend/src/type/event.ts`、`frontend/src/type/recordings.ts`、`frontend/src/type/measurement.ts`、`frontend/src/state/backendStore.ts`、`frontend/src/state/measurementStore.ts`、新規 `frontend/src/state/operationPolicy.ts`、`frontend/tests/backendStore.test.mjs`、`frontend/tests/measurementStore.test.mjs`、`frontend/tests/deviceApis.test.mjs`、新規 `frontend/tests/operationPolicy.test.mjs` | API契約の型、共有状態、測定期間・鮮度・結果不明の管理、操作条件。接続タイマーや画面を持たない。 |
| C：運転と設定 | 新規 `frontend/src/state/applicationRuntime.ts`、新規 `frontend/src/state/preferencesStore.ts`、新規 `frontend/tests/applicationRuntime.test.mjs`、新規 `frontend/tests/preferencesStore.test.mjs` | 初期化、重複しない定期更新、受信の要否、終了時の回収、設定の保持。A・Bの公開関数を使い、両者のファイルを変更しない。 |
| メイン：統合 | `frontend/src/main.tsx`、`frontend/src/App.tsx`、`frontend/src/views/ApiTestView.tsx`、`frontend/src/views/WebSocketPanel.tsx`、`frontend/src/views/SharedStatePanel.tsx`、`frontend/README.md`、本書、新規 `frontend/tests/coreIntegration.test.mjs` | 共通層の起動と既存画面への接続、境界の確認、結合テスト、文書更新。 |

API実装確認後、メインエージェントがA・B・Cの公開関数と型を先に合意させ、ファイル所有者を固定してから実装を開始する。担当ファイルへの修正が必要なら所有者へ差し戻す。メインエージェントが所有者と同時に書き換えない。

フロントエンドA・B・Cはgpt-6.1-sol、reasoning effort mediumで分担した。追加のファイルが必要なら、メインエージェントが先に所有者を決めて一覧を更新する。

## 10. メインエージェントの統合確認

各担当のテストと差分を確認した後、メインエージェントが以下を実行する。

- `frontend` で `npm test`、`npm run build`、`npm run lint`。
- 画面に依存しない結合テストで、初期化、ダミー機器の接続・設定・取得、数値・JPEG受信、同時記録、記録停止後の取得継続、停止・切断まで確認する。
- バックエンド側だけでの停止・設定変更・保存異常を状態取得へ反映する。
- WebSocket切断・復帰、サーバー再起動、測定ID変更で、状態と受信履歴が混在しないことを確認する。
- 応答の遅延・順序逆転・タイムアウト・初期化失敗・重複した起動終了・パネル購読解除を検証する。
- 接続復帰や再描画で、接続・取得・記録の開始要求が重複しないことを確認する。
- ブラウザーで、同じ共通状態が2パネルへ反映され、ビュー切り替えが取得・記録へ影響しないことを確認する。
- 実装前後の差分を確認し、担当外・バックエンド・依存関係の変更を混入させていないことを確認する。

バックエンドの受け入れ確認、ダミー機器との結合確認、実機確認は区別して報告する。実機や長時間・高負荷で確認していない動作を、確認済みとは扱わない。

## 11. フロントエンド担当間の公開インターフェース

以下を担当間の接続契約とする。実装で変更が必要なら、担当外のファイルを編集せずメインへ連絡する。

### A：通信

- `apiRawRequest`・`apiRequest` は既存の引数と戻り値を保ち、optionsに任意の `timeoutMs` を追加する。fetchへ独自オプションを渡さない。外部のAbortSignalにも従う。
- `ApiError` をexportし、`status: number | null` と `outcomeUnknown: boolean` を持たせる。HTTPエラーは結果が判明した失敗、変更要求のタイムアウト・通信断は結果不明として扱う。変更要求を自動再送しない。
- `getWebSocketClient(key, maxsize)` と既存 `websocketClient` を維持する。各clientは既存のgetSnapshot/subscribe/connect/disconnect/setLogReceivedに加え `acquire(): () => void` を提供する。
- `acquire()` は接続を必要とする購読者を登録し、戻り値で登録を解除する。connect/disconnectは診断用の手動接続要求の開始・解除とする。登録または手動要求が残る間は接続を維持し、予期しない切断後は間隔を延ばして再接続する。最後の要求を解除したら接続と再試行待ちを終了する。
- clientのsnapshotに既存項目と `leaseCount: number`、`manualConnection: boolean`、`lastReceivedAt: number | null`、`retryCount: number` を含める。
- `disposeWebSocketClients(): void` をexportし、画面の終了・HMR時に全接続と再試行を終了できるようにする。取得・記録APIは呼ばない。
- decodeEventはサーバーIDと測定IDを必須として検証する。解析成功時は共有measurementStoreへ渡す。受信キーからイベント型や取得元を決めない。

### B：状態・操作

- `MeasurementStatus` は新APIと同じ項目を持ち、`MeasurementEvent`・`CameraPreviewMetadata` に必須のserver_instance_id/measurement_idを追加する。RecordingStatusは第4節の契約に合わせる。
- backendStoreの既存のgetSnapshot/subscribeと操作メソッドを維持する。snapshotに `measurement: MeasurementStatus | null` と `synchronization: { status: "uninitialized" | "refreshing" | "ready" | "error"; lastUpdatedAt: number | null; error: string }` を追加する。
- `refresh(): Promise<void>` が機器一覧、接続済み機器の設定、測定状態、記録状態を取得・検証する。起動IDが一致しない応答を同じ現在値として確定しない。samplingは測定状態のsampling_device_idsから作る。
- `synchronization.status`は確定状態の有効性を表す。初回取得だけを`refreshing`とし、確認済みの通常状態は周期・手動の再取得中も`ready`を維持する。エラーは再取得成功まで保持し、再試行開始時に消さない。再取得中に操作が行われた場合、世代の変化を検出して古い応答を破棄し、最新状態を取り直す。
- 操作後に必要な確定値を再取得する。操作の重複、遅い応答、対象ごとのエラーを管理する。requestsの各値に `outcomeUnknown: boolean` を追加し、結果不明を通常の失敗と区別する。
- operationPolicyは `getOperationAvailability(snapshot, { operation, deviceId?, settings? }): { allowed: boolean; reason: string | null }` をexportする。operationはconnect/disconnect/startSampling/stopSampling/startRecording/stopRecording/updateSettings。初期化・鮮度・処理中・取得/記録・locked_settingsに基づいて判定し、バックエンドを最終判定とする。
- 操作メソッドも共通判定に従う。読み取り関数は初期化前にも使える。書き込みは初期化済みの状態を前提にする。
- measurementStoreは既存getSnapshot/subscribe/receiveを維持し、`setSession(serverInstanceId: string, measurementId: string | null)`、`setStreamState(deviceId, { connected: boolean, sampling: boolean })`、`refreshFreshness(maxAgeMs: number)` を提供する。
- 受信snapshotに `session: { server_instance_id: string; measurement_id: string | null } | null`、`needsResync: boolean`、`streams` を追加する。streamsの各値はconnected/sampling/lastReceivedAt/staleを持つ。lastReceivedAtはDate.now()のミリ秒。
- setSessionは異なる組の測定へ移ったとき履歴・画像・受信時刻を消去し、同じ組では消去しない。receiveは確認済みの組と一致するデータだけを保持し、未一致ならneedsResyncをtrueにしてfalseを返す。一致すればtrueを返す。確認なしに受信側のIDを現在値へ採用しない。
- setSessionはneedsResyncを解除する。状態設定が同一なら通知しない。接続断やmaxAgeMsを超えたデータはstaleとし、最後のデータは保持する。staleは取得停止の断定ではない。

### C：運転・設定

- `createApplicationRuntime(options?)` と単一の `applicationRuntime` をexportする。テスト用optionsにはbackendStore/measurementStore/getWebSocketClient/pollIntervalMs/staleAfterMsを指定できる。
- runtimeはgetSnapshot/subscribe、`start(): Promise<void>`、`refresh(): Promise<void>`、`dispose(): void` を提供する。snapshotは `phase: "idle" | "initializing" | "ready" | "error" | "disposed"` とerrorを持つ。
- startは重複呼び出しで初期化・タイマー・購読を重複させない。refreshは進行中ならその結果を共有し、周期を重ねない。dispose後の遅い応答で接続や周期を復活させない。
- readyのbackendStoreから確認済み測定組をmeasurementStoreへ設定し、取得中のカメラごとにキー付きmaxsize=1接続を、数値機器があればキーなし接続をacquireする。不要になった登録は解除する。機器の操作APIは自動実行しない。
- 各接続の状態をstreamsへ反映し、データの鮮度を確認する。needsResyncを検出した場合は共通のrefresh経路で状態を再確認する。
- 初期値はpollIntervalMs=1000、staleAfterMs=5000。staleAfterMsは受信の古さを表示する目安であり、機器の停止を確定する条件ではない。低FPS等の用途では変更可能とする。
- preferencesStoreはgetSnapshot/subscribe、`update({ theme?, defaultDirectory? })` を提供する。snapshotは `theme: "dark" | "light"`、`defaultDirectory: string`、`error: string`。初期値はdarkと空文字。`createPreferencesStore(storage?)` をexportし、StorageのgetItem/setItemだけに依存してテストできるようにする。
- 設定はバージョン付きのキーでlocalStorageへ保存する。読み込み・書き込みが使えない場合はエラーを通知し、メモリ上の設定操作を維持する。ライブラリは追加しない。

### メイン：統合

- main.tsxからruntimeを1回起動し、pagehide/HMR時にdisposeと通信の回収を行う。React StrictModeの表示用mount/unmountと運転の開始停止を結び付けない。
- AppはpreferencesStoreの配色を表示し、APIテストの入力・操作は共通状態管理へ接続する。状態表示にruntimeのphase、鮮度、結果不明を含める。
- UIへの新しい製品ビューや配置機能は追加しない。契約・単体・結合・ブラウザーの確認をメインが実行する。

## 2026-11-11 接続監視と取得エラーの通知

- `GET /api/devices` と個別機器のGETは、接続済みの実機に対して疎通確認を行う。UC480はSDKの機器一覧・機器情報、PM100Aはタイムアウト付きの識別問い合わせを使う。設定GETは保存済み設定を返す。
- `DeviceInfo.error` は接続・取得・後始末で保持している理由（文字列またはnull）。疎通が成功しても過去の取得エラーを自動解除しない。切断でスレッド終了・リソース解放が完了したら解除する。
- エラー状態でも切断を許可し、再接続前に後始末を行う。停止中のスレッドが残る場合はハンドルを閉じない。切断失敗は503と理由を返す。
- `sampling_device_ids` には、エラー検出後も終了待ちの取得スレッドが一時的に含まれ得る。フロントエンドはエラー機器の停止・切断に進めるよう、これを状態として受け入れる。
- 個別の取得失敗では他機器・Processor・記録を継続する。記録中の失敗は `metadata.json` の `acquisition_errors` に `reason: device_error`、`device_id`、`detected_at`、`message` を記録し、記録対象の `acquisition_error` に反映する。失敗検出時の記録IDを使い、次の記録へ持ち越さない。
- カメラSDKのネイティブ関数の応答時間と実機の抜線・復旧動作は、実機で確認する必要がある。
