# 表示性能の検証

製品のログ・テーブル・グラフを、分離したビルド・WebView2プロファイル・ローカルポートで検証する。実機や使用中のアプリには接続しない。Pythonの依存追加は不要。

プロジェクトのルートで実行する。

```powershell
.\.venv\Scripts\python.exe -B frontend/tests/performance/run.py --output "$env:TEMP/ui-performance-before"
# ソースの修正後、別の出力先で実行する。
.\.venv\Scripts\python.exe -B frontend/tests/performance/run.py --output "$env:TEMP/ui-performance-after"
```

各条件は初期化完了後3秒待ち、60秒計測する。ウィンドウは1200×900。ログは50件・1000件・非表示、テーブルは100行・1000行・非表示、グラフは履歴増加中・1000点からの追記を比較する。ログと数値を初期投入後、100ms間隔の有限入力列を追加する。画面が滞留するとJavaScriptタイマーの実行自体が遅れるため、`received`は条件ごとの実処理数として併記する。取得性能の証明には使わない。

CPUはWindowsの累積CPU時間を1秒間隔で読み、論理CPU数で正規化する。検証Pythonと、その子孫のWebView2プロセス群を区別し、条件ごとの平均・ピーク・標本をresult.jsonへ保存する。Pythonの値には検証サーバーと計測処理を含む。ホスト上の他アプリの負荷までは固定しない。比較中に別のGUI検証を並行実行しない。

CPU計測後、別の5秒区間でDOM変更・日時整形呼出し・50ms以上の処理を計測する。`timeFormats`/`formatMs`はDate.toLocaleString/toLocaleTimeString、`intlFormatCalls`/`intlFormatMs`は再利用するIntl.DateTimeFormatの呼出しであり、両者を合わせて比較する。`updates`はMutationObserverの通知回数で、Reactのrender回数ではない。行の追加・削除はtbodyのtrを数える。

設定GETは2秒ごとに製品のbackendStoreから送り、JSONの読取り・検証・ストア反映までの所要時間を測る。模擬サーバーの設定処理時間は別に記録する。タイムアウトは製品既定値の10秒を使う。

既存ビルドの再計測には`--skip-build`、特定条件には`--conditions graph1000`、CPU計測だけなら`--skip-profiles`を指定する。詳細計測だけをやり直す場合は、元のビルドを保持したまま別の出力先を指定する。

```powershell
.\.venv\Scripts\python.exe -B frontend/tests/performance/run.py --output "$env:TEMP/ui-profile-before" --build "$env:TEMP/ui-performance-before/build" --skip-build --profiles-only
```

実バックエンドのダミーパワーメーター1台で、1000件の受信後にも取得・設定変更・記録を継続する確認は、修正後のビルドを指定して実行する。

```powershell
.\.venv\Scripts\python.exe -B frontend/tests/performance/run_live.py --output "$env:TEMP/ui-performance-live" --build "$env:TEMP/ui-performance-after/build"
```

通常の共通runtimeで受信し、1000件到達後にテーブル・グラフ・ログを各60秒表示する。保存受付数とCSV行数、受信した全履歴の日時・経過時間・数値と記録開始後の全CSV行、最終メタデータを照合する。模擬入力の比較と実バックエンドの確認は分けて報告する。実機や長時間運転の保証には使わない。

複数系列の確認には上記の`run_live.py`へ`--comparison`を追加する。ダミーパワーメーター2台を取得・記録し、各1000件到達後に同じグラフで60秒表示する。設定GET、最終描画と2系列それぞれの全履歴件数、保存受付総数と2つのCSVの行数、記録区間の全受信値を照合する。記録先は試験ごとに別の出力先を指定する。

`run.py --conditions graphPair1000 --skip-profiles`は、模擬パワーメーター2台・各1000点へ100msごとに各1点を追加する。同じ回の受信時刻を25msずらし、時刻を揃える補間は行わない。グラフの更新方式を比較するときは、同じ検証画面から変更前後を別のビルドとして保存し、`--skip-build --build`で順番に測る。`graphUpdates`はChart.jsのafterRender通知回数、`graphUpdateAt`はその時刻、`graphPoints`は各系列のChart.jsに渡した元データ数（有限数値のみ）、`renderedPoints`はmin-max後の点数。欠損で分割した線は同じ系列として集計する。画面の走査・描画完了やフレームレートの厳密な計測ではない。

Reactの描画処理時間を調べる場合は、検証ビルド時だけ環境変数`PERFORMANCE_PROFILE_RENDER=1`を指定する。`react-dom/profiling`でグラフを囲むProfilerを有効にし、`--profiles-only --conditions graphPair1000`の5秒区間で`reactRenderMs`を記録する。これはReactが各コミットの描画処理に使った時間で、ブラウザーのレイアウト・ペイントや待機時間は含まない。CPU比較にはこの環境変数を指定しないビルドを使い、計測用の追加処理による影響を分ける。

全件保持の2時間表示は`--conditions graph2h graphPair2h --skip-profiles`で計測する。10Hz刻みの数値イベント72,000件を機器ごとに事前投入し、表示範囲を7200秒にする。受信と保持の件数、先頭の経過時刻0、各系列のChart.js元データに72,000点以上あることを検査する。1系列と2系列を別々に各60秒測り、CPU、Canvasの更新頻度、min-max後の点数、設定GETの所要時間を併記する。2時間の実取得を待つ試験ではなく、2時間相当の履歴での描画負荷測定である。

対照条件`--conditions graph2hRecent --skip-profiles`は、同じ72,000件を保持して直近30秒だけを表示する。履歴の保持と全点描画の負荷を分けて確認する。入力タイマーの目標は100msだが、描画でイベントループが滞留すると入力自体も遅れるため、10Hzでの受信・描画成功とは扱わず、`received`・`graphUpdates`・`graphUpdateAt`から実際の処理頻度を確認する。各条件は順番に実行し、CPU計測中は他のGUI検証やビルドを並行させない。
