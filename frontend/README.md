# frontend

## 共通テーマと表示密度

Astryx 0.7.11 の Neutral を継承した計測画面用テーマを使用する。共通の操作ボタン `OperationButton` は Astryx Button、既存のビュー・パネルは同じテーマの色・文字・余白トークンを使用する。

- `src/theme/measurementTheme.ts`：ダーク／ライトの配色と日本語向けのシステムフォント。青は操作・取得中、緑は成功・接続済み、赤は異常・未接続の印、黄は注意。
- `src/theme/density.css`：コンパクト・標準・ゆったりの寸法。文字、操作部品の高さ、ヘッダー、余白を一括調整する。パネル間の隙間、アイコン、グラフ・画像の倍率は密度に連動させない。
- `src/theme/AppTheme.tsx`：配色と密度の適用箇所。既定は `standard`。将来の設定画面では `density` に `compact` / `standard` / `comfortable` を渡す。密度の選択UI・保存は未実装。

| 密度 | 本文 | 補助文字 | 強調文字 | 操作部品の高さ | 基本余白単位 |
|---|---:|---:|---:|---:|---:|
| compact | 11px | 10px | 12px | 22px | 3px |
| standard | 12px | 11px | 14px | 26px | 4px |
| comfortable | 14px | 12px | 16px | 32px | 6px |

配色の定義を変更した場合と Astryx を更新した場合は、`frontend` で `npm run theme:build` を実行する。`src/theme/generated/` は生成物としてソースと一緒に管理し、直接編集しない。密度CSSだけの変更では再生成は不要。Webフォントは読み込まず、Windowsのフォントと代替フォントを使用する。

既存CSSの汎用ボタン指定は `.ui-button` を除外し、Astryxのスタイルを上書きしない。新しい共通色・寸法は各ビューへ直接書かず、上記テーマへ追加する。

デバイス管理は Astryx の Card（枠線なし）・Text・Badge（台数）・Button（再検索と接続操作）を使う。機器行は選択枠と明るいホバー表示、右側は半円形の操作ボタンと横並びの状態表示とする。機器情報も同じカード・文字・余白を使う。

React・TypeScript・Viteを使用。パネルの配置・境界ドラッグ・並べ替えにはreact-mosaic-component 7.2.1を使用する。起動時は2行3列で、上段にデバイス管理・イメージ・カメラ設定、下段に測定・記録・グラフ・パワー設定を表示する。1パネル・左右2パネル・上下2パネルにも切り替えられる。`design-demo.html`は見本データを使うUIデモ。

配置の追加・変更は`src/workspace/layouts.ts`で行う。各定義は`id`・`title`・`panels`・`layout`を持ち、`layout`の葉にはパネルIDを指定する。分割はv7形式の`{ type: "split", direction: "row" | "column", children: [...] }`を使う。`row`は左右、`column`は上下。`splitPercentages`は省略時に均等分割、指定時は領域数と同じ長さで合計100にする。異なるレイアウトで同じパネルIDを使う場合は、初期ビューとタイトルを揃える。`viewId`は初期値で、利用者が変更したビューを配置切り替え時に戻さない。

ヘッダーをドラッグすると、他パネルの辺へ再配置でき、中央へのドロップで入れ替えられる。境界は分割ツリーごとに動くため、全体を左右に分けた縦境界は全体として動く。ヘッダーの分割ボタンは、その領域を左右に等分し、右側に新しいID・同じ現在ビューのパネルを作る。新しいパネルへ選択機器・倍率・未適用入力は複製しない。最大化ボタンはウィンドウ内をそのパネル1枚の表示に切り替え、再押下で元の配置と分割率へ戻す。最大化中の分割・閉じるは元の配置に対して行い、別の配置を選ぶと最大化を解除する。閉じる操作は選択中の配置からだけ取り除き、機器の取得・記録を停止しない。全パネルを閉じた場合は、空表示の「初期配置に戻す」で復元できる。配置・分割率・追加パネルはレイアウトごとに現在のウィンドウ内で保持し、アプリ終了後の復元は行わない。タブ配置と独自の境界キーボード操作は提供しない。

「測定・記録」では取得中の機器と記録状態、保存条件、記録開始停止、実際の保存先・対象別の保存結果を確認できる。保存先はバックエンド環境の親フォルダを入力し、カメラの動画形式は記録条件で一括選択する。取得と記録は独立し、開始後に取得した機器は既存の記録へ追加されない。停止失敗時は全体を再取得してから「停止を再試行」を使う。以降のUIは機能確認に必要な最小実装とし、外観の仕上げは全体実装後に行う。

「データのテーブル」「グラフ」は同じ数値履歴を表示する。テーブルは直近100/500/1,000件、列選択、追従ON/OFF、グラフは項目と直近10/30秒・1/10/30分・1/2時間・保持全範囲を選べる。設定はパネル・対象機器ごとに保持する。power_wの単位はW、他の単位は推測しない。現在の測定期間で受信した数値イベントを機器別に全件保持し、測定ID・バックエンド起動IDが変わると履歴を消去する。グラフに実際の保持範囲を表示する。詳細は[設計計画5.6節](../agent-docs/フロントエンドUI設計・実装計画.md)を参照。

履歴本体は`measurementHistory.ts`の内部配列へ追記し、受信ごとの全履歴コピーは行わない。公開スナップショットは件数・時刻範囲・項目一覧と、その件数までを読み出す関数を持つ。グラフとテーブルは表示範囲だけを配列へ取り出し、画面へ渡した配列を変更しない。通常は時刻順の追記として二分探索で時間幅を切り出し、時刻逆転がある場合は表示用に並べ替える。グラフは変更のない系列と既存点の変換結果を再利用する。Chart.jsのCanvas描画とmin-max方式で表示点数を減らし、元の全履歴は維持する。欠損箇所で系列を分割し、線の分断を維持する。ツールチップとアニメーションは無効にする。更新通知は新しいデータが届いた次の画面フレームへまとめる。ログ・テーブルの表示通知は200ms間隔を維持する。検証条件と計測方法は[表示性能の検証](tests/performance/README.md)を参照。

「イメージ」は共有ストアの最新JPEGを表示する。カメラ選択、パネルに収める表示、25〜400%の倍率を扱い、倍率はパネル・カメラごとに保持する。プレビュー寸法と最終受信を表示し、取得停止・通信断では最後の画像を残す。画像更新・非表示・対象変更時には表示用URLを解放する。表示倍率は保存画像・ROIを変更しない。ROI編集や画像解析は未実装。

導入・起動・テストは [導入・起動方法](../doc/起動方法.md)、デザインの基準は [UIデザインルール](../agent-docs/UIデザインルール.md) を参照。
初期テンプレートはcreate-vite。付属素材の出所とライセンスは [依存ライブラリとライセンス](../THIRD_PARTY_NOTICES.md) に記載。

## API呼び出しと共有状態

- `src/api/devices.ts`：一覧・探索・接続・切断・設定取得／適用・取得開始／停止／状態取得。
- `src/api/recordings.ts`：記録開始・停止・全体状態取得。記録開始時の対象は、その時点で取得中の全機器。
- `src/state/backendStore.ts`：機器一覧、適用済み設定、取得・記録状態、操作ごとの処理中件数とエラーを保持する。ビューからの操作にはこのストアのメソッドを使う。APIモジュールの関数を単独で呼び出した場合は、ストアへ自動反映されない。
- `src/state/measurementStore.ts`：デバイスごとに現在の測定期間の全数値イベントと最新のJPEG Blobを保持する。履歴への追記と読出しは`measurementHistory.ts`が担当する。JPEGの描画やBlob URLの作成は表示側が担当する。

各ビューは `useSyncExternalStore(backendStore.subscribe, backendStore.getSnapshot)` で同じ状態を参照する。受信データは同じ形式で `measurementStore` を参照する。表示対象・倍率・未適用入力は各パネルで保持し、ストアには保存しない。購読解除では共有データを消去せず、機器の取得・記録も停止しない。共有範囲は同一ウィンドウ内。

デバイス設定は`src/devices/<機器種類>/`に型・応答検証・入力変換・適用方法・フォームをまとめる。追加時の共通編集先は`settingsContracts.ts`と`settingsForms.tsx`の2箇所。共通設定型は登録から導出する。詳しい追加手順と送信条件は[デバイス設定画面の構成と送信制御](../agent-docs/デバイス設定画面の構成と送信制御.md)を参照。

カメラの露光時間・ゲインは150ms間隔の即時反映、それ以外の既存項目はボタン適用を初期割当とする。`state/settingsUpdates.ts`が同一機器への送信を全パネルで直列化し、送信直前の最新設定へ変更項目だけを重ねる。非表示・対象変更では待機分を取り消して入力を保持する。失敗・結果不明や取消し後の入力は、状態確認後に「即時項目を再適用」から明示的に再送する。「元に戻す」は未反映入力の破棄で、適用済みの機器設定を以前の値へ戻さない。

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

通常の周期更新では確認済み状態を維持し、ボタンや操作不可の説明を再取得のたびに切り替えない。初回未確認・操作中・同期エラー・結果不明の操作制限は維持する。フッターの再取得ボタンは初期化中と手動押下による再取得中だけ無効にする。

未取得の一覧・記録状態は `null`、機器別の設定・取得状態は未登録になる。失敗時は直前の値を残して `requests` にエラーを記録するため、値とエラーを併せて表示する。重複GETはまとめ、同じ対象への変更操作は重複送信しない。設定変更等より前のGET応答は、適用後の値へ反映しない。

APIテスト画面はHTTPの応答本文をそのまま表示し、機器・記録APIの応答後に `backendStore.refresh()` で共有状態を再取得する。各APIテストビューの「共有データ」を展開すると、他パネルでの操作結果も確認できる。初回や外部からの変更を確認する場合は「状態を再取得」を使う。applicationRuntimeが起動時と1秒周期で状態を取得する。測定・記録状態の識別IDと対象情報、数値・画像イベントの起動ID・測定IDは実装済み。既存APIの診断用呼び出しも利用できる。

WebSocketは `getWebSocketClient(key, maxsize)` で購読条件ごとの接続を共有する。既定の `websocketClient` は `/ws`、カメラ用には `getWebSocketClient(deviceId, 1)` を使う。`connect()`・`disconnect()` は明示操作で、同じ購読条件の全パネルへ反映される。acquire()で中核の接続要求を登録し、最後の登録と手動要求を解除したときに接続・再試行を終了する。必要な間は切断後に自動再接続する。

受信時は購読キーからデータ種別を決めず、テキストの `measurement` とバイナリの `camera_preview` を解析して `measurementStore` へ反映する。画像の `timestamp_ns` は文字列のまま保持し、数値の時間軸には `elapsed_s` を使う。不正なメッセージは共有データへ追加せず、WebSocketの `dataError` とログへ理由を残す。APIテストのWebSocket欄では購読キー・Queue上限の選択と画像メタデータの確認まで行える。製品用の画像・グラフ・ROIビューは未実装。

中核の追加実装は `applicationRuntime`（初期化・周期取得・受信管理）、`operationPolicy`（操作可否と理由）、`preferencesStore`（配色・標準保存先の共有保存）。HTTP変更要求のタイムアウトは結果不明として扱い、自動再送しない。受信データは確認済みのサーバー・測定IDと一致する場合だけ採用し、通信断時は最後のデータを保持してstaleを表示する。

追加APIの契約・制約・担当範囲は [中核API仕様](../agent-docs/core-api-contract.md) を参照。測定状態APIと、記録状態のID・開始時対象・保存結果・設定制限に対応している。記録IDと対象情報は停止後も保持する。数値・画像イベントはQueue投入時の測定期間を引き継ぎ、送信時に現在のIDへ置き換えない。ダミーバックエンドの受信形式の確認と、共通層を使った画面操作・実機確認は区別する。

## 共通UIとビュー状態の保持

通常画面のビューはログ・APIテスト・デバイス管理・個別デバイスの4種類。測定・記録、テーブル、グラフ、イメージなどは後続で追加する。

`Workspace`は、一度表示したビューをパネルID・ビューIDごとにReactの`Activity`で保持する。Portalの描画先コンテナーはパネルごとに固定し、`MosaicPanel`がMosaicの表示先へコンテナーを移す。配置から外れたパネルは非表示の保管領域へ戻す。ビュー・配置の切り替えやドラッグ後も、`useState`の入力・選択・表示設定とDOMの折りたたみ状態を保持する。閉じたパネルの内容もウィンドウ終了まで保持するが、閉じた配置へ自動で再追加はしない。未表示のビューは初めて開くまで描画しない。アプリを終了した場合の保存・復元は、この仕組みには含めない。

非表示中はEffectの購読を解除し、再表示時に再購読する。取得・記録はウィンドウ共通のruntimeと共有ストアが管理するため、ビューのEffectで開始・停止しない。機器ごとの未適用入力を残す場合は、各ビューでデバイスIDをキーに入力を保持する。パネル間でこの入力を共有しない。

追加ビューには次の共通部品を利用できる。

| 部品 | 役割 |
|---|---|
| `components/View.tsx`の`ViewLayout` | ツールバー・スクロールする本文・本文外の操作欄を配置する |
| 同ファイルの`ViewMessage` | 案内・注意・エラーを文字で表示する |
| 同ファイルの`DeviceDataView` | `deviceId`と`source`（`measurement`または`preview`）に対応する空表示・受信状態を共有ストアから選ぶ。受信済みデータがあれば、通信断や更新停止でも本文を残し、最終受信時刻を添える |
| `components/OperationButton.tsx` | `request`を既存の`operationPolicy`で判定し、操作不可のボタンを無効化する。理由はツールチップと支援技術向け説明に渡し、ボタン横には表示しない。`onClick`から共通ストアを呼び、失敗時の処理は呼び出し側で行う |
| `components/BackendStatus.tsx` | `BackendStatusSummary`で最終確認時刻と「状態を再取得」をウィンドウ共通フッターへ表示する。通常状態や更新中の説明文は表示しない。`BackendStatus`はエラー・結果不明の詳細を上部に表示する |

周期的な状態更新中も、既存データを初期化中の表示へ置き換えない。共通パネルはCSSの最小高さに達したら本文を非表示にし、本文の入力をフォーカス対象から外す。再拡大時に内容を復元し、ヘッダーを残す。

状態表示の判定は`tests/viewStatus.test.mjs`で確認する。実際のReact・DOM・WebView2での切り替えとレイアウトは、[ワークスペース検証](tests/workspace/README.md)を参照。

## デバイス管理と個別デバイス

ウィンドウの起動後、バックエンドの状態を初めて確認できた時点で機器を一度自動探索する。自動探索に失敗した場合はエラーを表示し、「デバイス管理」の「再検索」から再試行できる。一覧から機器を選び、接続・切断する。この画面の接続操作では、接続と状態確認の成功後に自動で取得を開始する。取得開始または状態確認に失敗した場合はエラーを表示し、自動再送や自動切断はしない。選択はパネルごとに独立し、接続状態は全パネルで共有する。取得中の機器を切断すると、その機器の取得も停止する。

機器一覧にはモデル名、種類、シリアル番号、接続状態と、接続済み機器の取得状態を表示する。各行のボタンから接続・切断を操作する。行を選ぶと、アドレスと機器IDを含む機器情報を表示する。

「個別デバイス」では接続済み機器を選んで設定を編集する。デバイス管理を同時に表示する必要はない。

- パワーメーター：波長 [nm]、測定レンジ [W]、自動レンジ、帯域幅、内部平均化の点数。
- カメラ：露光時間 [ms]、ゲイン、ゲインブースト、FPS。APIの露光時間 [s]との変換は表示・送信時に行う。画像サイズ・画素形式・保存ROIは現在値を表示する。

未適用入力はパネルと機器の組み合わせごとに保持し、周期GETや他パネルの操作で上書きしない。バックエンドの起動IDが変わった場合、以前の起動の入力を新しい機器設定へ引き継がない。設定の取得操作も未適用入力を残す。「元に戻す」は最後に確認した適用済み値へ戻す。

適用時には、編集した項目だけを共通ストアの最新の適用済み設定へ重ね、設定一式を送る。他パネルが変更した未編集項目や、表示のみの画像情報・保存ROIを古い値へ戻さない。適用済み値が更新された場合は画面に通知する。成功を確認したら未適用入力を消し、確定した値を表示する。失敗や結果不明の場合は入力を残し、自動再送しない。

記録対象の設定ロックは既存の`locked_settings`・`operationPolicy`に従う。記録中のFPS編集を無効にし、開始前から未適用のFPS変更があった場合も適用を拒否する。許可されている露光・ゲインの変更は行える。UIは有限数、正数、平均化の整数などを検証するが、実機固有の上限・選択肢をデモの値で固定しない。現在のダミーカメラの露光・ゲイン設定は値の保持のみで、合成画像の明るさには反映しない。

実バックエンドとダミー機器を使う画面操作の検証は[機器UI検証](tests/devices/README.md)を参照。

## pywebviewでの中核検証と設定保存

記録状態は全体更新だけでなく、個別更新・記録開始・記録停止の応答も検証してから共有状態へ反映する。不正な応答では最後の確定値を保持し、変更要求の適用結果が確認できなければ結果不明として扱う。個別GETの成功だけでは結果不明を解除せず、全体再取得で確認する。

デスクトップ起動はプライベートモードを無効にし、既定では `%LOCALAPPDATA%/MarkLab/MultiDeviceControlGui/WebView2` にアプリ専用プロファイルを保存する。配色と標準保存先は既存のlocalStorageキーを使う。同じプロファイルと同じオリジン（スキーム・ホスト・ポート）で起動し直した場合に設定を復元する。

[pywebview検証手順](tests/pywebview/README.md) のランチャーで、実機を使わず実際のWebView2を検証できる。検証画面は共通ストア経由で操作し、既存のAPI診断画面はそのまま維持する。検証用の保存プロファイル・ポート・ビルド先は通常起動から分ける。

実バックエンドとダミードライバーを使う結合確認は `tests/pywebview/run_real.py` で実行する。
分離したEdgeブラウザーとWebView2で、取得・実CSV／動画保存・異常時の状態反映・再接続・終了を確認する。
模擬APIの検証とは別に実行し、保存ファイルと結果JSONを指定した出力先へ残す。実機の動作を確認する検証ではない。

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
