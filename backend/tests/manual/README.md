# PM160・uc480の実機テスト

機器ごとにファイルを分けています。コマンド引数は使いません。
各ファイルの先頭にある設定を編集し、リポジトリ直下のPowerShellから実行します。

| ファイル | 用途 |
|---|---|
| `check_pm160.py` | パワーメータの探索・接続・設定・取得・切断 |
| `check_uc480.py` | カメラの探索・接続・設定・画像取得・切断 |
| `hardware_check_common.py` | Queueからのデータ取得、PNG保存、ログ・JSON保存の補助処理 |

## 実行前

- PM160：NI-VISAと機器ドライバーを用意し、Power Meter Driver SwitcherでNI-VISA側を選びます。
- uc480：ThorCam用のuc480ドライバー・DLLを用意し、PythonとSDKの64bitをそろえます。
- `.venv` にPyLabLib・PyVISAなど、各機器クラスの依存ライブラリが必要です。
- 同じ機器を使用しているThorCam・OPM・本アプリを閉じます。

通常の自動テストでは実機に接続しません。以下のファイルを明示的に実行した場合だけ、探索または接続を行います。

## パワーメータ

1. `check_pm160.py` の先頭を `SCAN_ONLY = True` にして実行します。

```powershell
.\.venv\Scripts\python.exe -m backend.tests.manual.check_pm160
```

2. 表示された `VISAアドレス一覧` から、使用するPM160のアドレスを `VISA_ADDRESS` へ貼り付けます。
3. `SCAN_ONLY = False` に変更します。必要なら `CAPTURE_SECONDS` も変更します。

```python
SCAN_ONLY = False
VISA_ADDRESS = "USB0::0x1313::0x807B::実際の番号::INSTR"
CAPTURE_SECONDS = 5.0
```

4. 同じコマンドをもう一度実行します。

現在のPM160ドライバーは探索時の先頭文字を `URB` と比較しています。そのためドライバーの探索結果が空でも、VISA一覧に機器が表示される場合があります。テストは両方を表示し、指定したアドレスで接続以降を確認します。ドライバー本体の探索判定は変更していません。

## カメラ

1. `check_uc480.py` の先頭を `SCAN_ONLY = True` にして実行します。

```powershell
.\.venv\Scripts\python.exe -m backend.tests.manual.check_uc480
```

2. 探索結果の `serial_number` を `CAMERA_SERIAL` へ貼り付けます。
3. `SCAN_ONLY = False` に変更します。低FPS・長い露光の場合は `CAPTURE_SECONDS` を増やします。

```python
SCAN_ONLY = False
CAMERA_SERIAL = "実際のシリアル番号"
CAPTURE_SECONDS = 5.0
```

4. 同じコマンドをもう一度実行します。

## 各ファイルで確認する手順

コード内に番号付きのコメントを付けています。

1. 指定した機器へ接続する。
2. 機器情報と現在の設定を記録する。
3. 停止中に現在の設定を再適用し、読み戻し値を確認する。
4. 1回目のデータ取得を行う。途中で設定を再適用し、その設定通知の後にもデータが届くことを確認して停止する。
5. 2回目のデータ取得を行い、停止後に再開できることを確認する。
6. 切断する。失敗やCtrl+Cの場合も切断を試みる。
7. 結果をJSONへ保存する。

`CAPTURE_SECONDS = 5.0` なら5秒の取得を2回行います。
現在値の再適用を確認するテストで、任意の新しい値や全設定範囲を試すテストではありません。
接続時にドライバーがパワーモード／8bit画像形式へ設定します。接続前のモードや16bit形式への自動復元は行いません。

## 保存される結果

`OUTPUT_ROOT` の下に、機器名と日時を付けたフォルダーを作成します。

```text
test-results/hardware/
  pm160/日時/
    test.log
    result.json
  uc480/日時/
    test.log
    result.json
    uc480-cycle-1.png
    uc480-cycle-2.png
```

- `test.log`：確認した段階とエラーのトレースバック。
- `result.json`：成功・失敗、機器情報、設定、取得件数、最後のデータ。失敗時は `error.stage` を確認します。
- PNG：各取得の最初の1枚。取得した画像全体を保存し、保存ROIでの切り出しは行いません。

探索だけの場合、`passed: true` は探索処理が完了したことを示します。機器を検出・接続できたことの保証ではありません。
接続テストの場合、`passed: true` は上の手順が完了したことを示します。
PowerShellの `$LASTEXITCODE` は成功なら0、テスト失敗なら1です。

PNGを開いて、被写体、明るさ、色、縦横が期待どおりかを目視確認してください。
パワーはW単位なので、既知の光源や機器の表示と比較してください。
測定精度、長時間の安定性、欠損率、CPU使用率、アプリ画面、CSV・動画保存は、このテストの確認範囲に含みません。
