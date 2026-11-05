# frontend

React・TypeScript・Viteを使用。通常画面はAPI通信テスト、`design-demo.html` は見本データを使うUIデモ。

導入・起動・テストは [導入・起動方法](../doc/起動方法.md)、デザインの基準は [UIデザインルール](../UIデザインルール.md) を参照。
初期テンプレートはcreate-vite。付属素材の出所とライセンスは [依存ライブラリとライセンス](../THIRD_PARTY_NOTICES.md) に記載。

## API呼び出しと共有状態

- `src/api/devices.ts`：一覧・探索・接続・切断・設定取得／適用・取得開始／停止／状態取得。
- `src/api/recordings.ts`：記録開始・停止・全体状態取得。記録開始時の対象は、その時点で取得中の全機器。
- `src/state/backendStore.ts`：機器一覧、適用済み設定、取得・記録状態、操作ごとの処理中件数とエラーを保持する。ビューからの操作にはこのストアのメソッドを使う。APIモジュールの関数を単独で呼び出した場合は、ストアへ自動反映されない。
- `src/state/measurementStore.ts`：デバイスごとに数値イベントの直近1,000件と最新のJPEG Blobを保持する。JPEGの描画やBlob URLの作成は表示側が担当する。

各ビューは `useSyncExternalStore(backendStore.subscribe, backendStore.getSnapshot)` で同じ状態を参照する。受信データは同じ形式で `measurementStore` を参照する。表示対象・倍率・未適用入力は各パネルで保持し、ストアには保存しない。購読解除では共有データを消去せず、機器の取得・記録も停止しない。共有範囲は同一ウィンドウ内。

```tsx
import { useSyncExternalStore } from "react";
import { backendStore } from "./state/backendStore";

function DeviceCount() {
    const state = useSyncExternalStore(backendStore.subscribe, backendStore.getSnapshot);
    return <span>{state.devices?.length ?? 0}台</span>;
}

// 接続後の機器情報、適用後の設定、取得・記録状態を共有ストアへ反映する。
await backendStore.connectDevice(deviceId);
await backendStore.refreshSettings(deviceId);
await backendStore.startSampling(deviceId);
```

未取得の一覧・記録状態は `null`、機器別の設定・取得状態は未登録になる。失敗時は直前の値を残して `requests` にエラーを記録するため、値とエラーを併せて表示する。重複GETはまとめ、同じ対象への変更操作は重複送信しない。設定変更等より前のGET応答は、適用後の値へ反映しない。

APIテスト画面はHTTPの応答本文をそのまま表示し、機器・記録APIの応答後に `backendStore.refresh()` で共有状態を再取得する。各APIテストビューの「共有データ」を展開すると、他パネルでの操作結果も確認できる。初回や外部からの変更を確認する場合は「状態を再取得」を使う。applicationRuntimeが起動時と1秒周期で状態を取得する。新しい測定状態APIと記録状態拡張は現在未実装のため、中核の初期化はunsupportedを表示する。既存APIの診断用呼び出しは引き続き利用できる。

WebSocketは `getWebSocketClient(key, maxsize)` で購読条件ごとの接続を共有する。既定の `websocketClient` は `/ws`、カメラ用には `getWebSocketClient(deviceId, 1)` を使う。`connect()`・`disconnect()` は明示操作で、同じ購読条件の全パネルへ反映される。acquire()で中核の接続要求を登録し、最後の登録と手動要求を解除したときに接続・再試行を終了する。必要な間は切断後に自動再接続する。

受信時は購読キーからデータ種別を決めず、テキストの `measurement` とバイナリの `camera_preview` を解析して `measurementStore` へ反映する。画像の `timestamp_ns` は文字列のまま保持し、数値の時間軸には `elapsed_s` を使う。不正なメッセージは共有データへ追加せず、WebSocketの `dataError` とログへ理由を残す。APIテストのWebSocket欄では購読キー・Queue上限の選択と画像メタデータの確認まで行える。製品用の画像・グラフ・ROIビューは未実装。

中核の追加実装は `applicationRuntime`（初期化・周期取得・受信管理）、`operationPolicy`（操作可否と理由）、`preferencesStore`（配色・標準保存先の共有保存）。HTTP変更要求のタイムアウトは結果不明として扱い、自動再送しない。受信データは確認済みのサーバー・測定IDと一致する場合だけ採用し、通信断時は最後のデータを保持してstaleを表示する。

将来APIの契約・制約・担当範囲は [中核API仕様](docs/core-api-contract.md) を参照。バックエンド変更は取り消しており、現行バックエンドと新中核の実データ結合はAPI実装後の確認対象。モック結合テストと実機確認は区別する。

## pywebviewでの中核検証と設定保存

記録状態は全体更新だけでなく、個別更新・記録開始・記録停止の応答も検証してから共有状態へ反映する。不正な応答では最後の確定値を保持し、変更要求の適用結果が確認できなければ結果不明として扱う。個別GETの成功だけでは結果不明を解除せず、全体再取得で確認する。

デスクトップ起動はプライベートモードを無効にし、既定では `%LOCALAPPDATA%/MarkLab/MultiDeviceControlGui/WebView2` にアプリ専用プロファイルを保存する。配色と標準保存先は既存のlocalStorageキーを使う。同じプロファイルと同じオリジン（スキーム・ホスト・ポート）で起動し直した場合に設定を復元する。

[pywebview検証手順](tests/pywebview/README.md) のランチャーで、実機を使わず実際のWebView2を検証できる。検証画面は共通ストア経由で操作し、既存のAPI診断画面はそのまま維持する。検証用の保存プロファイル・ポート・ビルド先は通常起動から分ける。

以下は初期テンプレートの設定メモ。

## React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type-aware lint rules:

```js
export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...

      // Remove tseslint.configs.recommended and replace with this
      tseslint.configs.recommendedTypeChecked,
      // Alternatively, use this for stricter rules
      tseslint.configs.strictTypeChecked,
      // Optionally, add this for stylistic rules
      tseslint.configs.stylisticTypeChecked,

      // Other configs...
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])

```

You can also install [eslint-plugin-react-x](https://npmx.dev/package/eslint-plugin-react-x) and [eslint-plugin-react-dom](https://npmx.dev/package/eslint-plugin-react-dom) for React-specific lint rules:

```js
// eslint.config.js
import reactX from 'eslint-plugin-react-x'
import reactDom from 'eslint-plugin-react-dom'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...
      // Enable lint rules for React
      reactX.configs['recommended-typescript'],
      // Enable lint rules for React DOM
      reactDom.configs.recommended,
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])

```
