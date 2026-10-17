# ThorCamをPythonで操作する

Windowsでカメラを操作するための導入メモ。PyLabLib 1.4.5のAPI・ソースと照合した操作例を記載する。
本プロジェクトはUC480Cameraを使う実機対応を予定。TLCamera系の説明も参考として残す。

## 1. ThorCam（+ SDK）インストール

以下の公式サイトを開き、「ThorCam Software」→「ThorCam Software for 64-Bit Windows」→「Download」でソフトをダウンロード。  
[公式サイト](https://www.thorlabs.com/software-pages/ThorCam)

これには、１．ThorCamカメラ用のインターフェイス、２．ドライバ、３．SDKが含まれる。  
通常はPCとカメラをUSBで接続し、ThorCamカメラ用のインターフェイスから映像を表示・保存する。  

カメラをPythonで操作する場合はSDKを使用。

同じカメラをPythonで操作する前に、ThorCam側の接続を閉じる。Python・SDK・ドライバーの32-bit / 64-bitをそろえる。

## 2. カメラ制御用のPythonライブラリをインストール

PyLabLibは様々な実験デバイスのSDKをラッパーし、それらのデバイスをPythonから制御したり自動化したりするためのライブラリ。  
Thorlabsのデバイスは `pylablib.devices.Thorlabs` モジュールから制御できる（[参考記事](https://qiita.com/h1na/items/d305d49b5a27e92d132a)）。

Python環境で以下を実行しインストール

```powershell
python -m pip install "pylablib==1.4.5" pillow opencv-python
```

PillowはPNG保存、OpenCVは動画保存に使用。NumPyはPyLabLibの依存として導入される。
uc480系には `uc480` / `ueye` のバックエンドがあり、カメラに使われているドライバーに合わせて選択する。DLLの配置は[公式導入資料](https://pylablib.readthedocs.io/en/stable/devices/uc480.html)を参照。

PyLabLibのライセンスはGPLv3。PyLabLibと結合したプログラムを再配布する場合は、GPLv3の条件に従う必要がある。
メーカー製SDK・ドライバには各提供元の利用条件が適用される。詳細は [依存ライブラリとライセンス](../THIRD_PARTY_NOTICES.md) を参照。

## 3. PyLabLibからThorCamの操作

PyLabLibで操作できるThorlabsカメラは以下の2系統。

- **TLCamera 系**：主に最近のThorlabsカメラで使用される。ThorCam SDKを使用。
- **uc480/uEye 系** (DCCxxxx 系)：主に古いDCCxxxxシリーズのカメラで使用される。uc480 SDKを使用。

カメラの形式はThorCamインターフェイスのカメラ選択画面の表示から確認できる。

### 3.1 シリアル番号の取得

#### TLCamera 系

接続しているカメラのシリアル番号を以下で取得。

- pylablib.devices.Thorlabs.list_cameras_tlcam()
- pylablib.devices.Thorlabs.TLCamera.list_cameras()


```python
# 接続デバイスのシリアル番号一覧を取得
from pylablib.devices.Thorlabs.TLCamera import list_cameras
serial_list = list_cameras()
if not serial_list:
    raise RuntimeError("ThorLabsカメラが接続されていません。")
serial = serial_list[0]
print(serial)
```

#### uc480/uEye 系 (DCCxxxx 系)

DCCxxxx 系カメラの場合は代わりに以下を使用してカメラ一覧を取得し、.dev_idでデバイスIDを取得。

- pylablib.devices.uc480.uc480.list_cameras()


```python
# DCCxxxx 系カメラのデバイスIDを取得する例
from pylablib.devices.uc480.uc480 import list_cameras   
cameras = list_cameras()
if not cameras:
    raise RuntimeError("ThorLabsカメラが接続されていません。")
dev_id = cameras[0].dev_id
print(dev_id)
```

### 3.2 ThorlabsTLCamera / UC480Camera クラス 

#### TLCamera 系

**ThorlabsTLCamera(serial=None)**
([ドキュメント](https://pylablib.readthedocs.io/en/stable/.apidoc/pylablib.devices.Thorlabs.html#pylablib.devices.Thorlabs.TLCamera.ThorlabsTLCamera))

カメラ操作用のクラスです。

- `serial`：`list_cameras()` で取得したシリアル番号を指定する。（Noneの場合は`list_cameras()` の先頭のデバイス）


```python
from pylablib.devices.Thorlabs.TLCamera import ThorlabsTLCamera

# ThorlabsTLCamera クラスのインスタンスを作成
cam = ThorlabsTLCamera(serial=serial)  
cam.close()
```

#### uc480/uEye 系 (DCCxxxx 系)
**UC480Camera(dev_id=dev_id, backend="uc480")**
([ドキュメント](https://pylablib.readthedocs.io/en/latest/.apidoc/pylablib.devices.uc480.html#pylablib.devices.uc480.uc480.UC480Camera))

カメラ操作用のクラスです。

- `dev_id`：`list_cameras()` で取得したデバイスIDを指定する。省略時は `cam_id=0` の指定で利用可能な最初のカメラを使用する。


```python
from pylablib.devices.uc480.uc480 import UC480Camera

cam = UC480Camera(dev_id=dev_id)  # 最初のデバイスを使用。 ほかのデバイスを使用する場合は適宜変更する。
cam.close()
```


## 3.3 よく使用するメソッド一覧（UC480Camera）

### 初期化・クローズ

**open()**

* カメラへの接続を開きます。
* 通常はコンストラクタ `UC480Camera(dev_id=..., backend="uc480")` で自動的に開くため、明示的呼び出しは少なめです。

**close()**

* カメラとの接続を閉じ、リソースを解放します。
* with文を使うとスクリプト終了や例外発生時に自動的に呼ばれるため安全です。

**is_opened()**

* デバイスが現在接続されているかどうかを真偽値で返します。

---

### 情報の取得

**get_device_info()**

* カメラのモデル・シリアル・USBバージョン・DLLバージョンなどの情報を取得します。
* 返り値は `(cam_id, model, manufacturer, serial_number, usb_version, date, dll_version, camera_type)` というタプルです。

  * TLCamera 版とは要素内容が少し異なる点に注意してください。

**get_detector_size()**

* センサー全体の画素数を `(width, height)` のタプルで返します。

---

### パラメータの取得・設定

**get_exposure() / set_exposure(exposure)**

* 露光時間（秒）を取得・設定します。

**get_gains() / set_gains(master=None, red=None, green=None, blue=None)**

* `get_gains()`：

  * `(master, red, green, blue)` のタプル（各チャネルのゲイン係数）を返します。
* `set_gains(...)`：

  * 指定したチャネルだけ値を変更し、`None` のチャネルは現状維持します。
* モノクロ DCC カメラでは、通常は `master` だけを使う運用で十分です。

**get_gain_boost() / set_gain_boost(enabled)**

* ゲインブースト機能の有効／無効を取得・設定します。
* 高感度モードと考えて良いですが、ノイズも増えるので必要な場合だけ有効にします。

---

### 撮影関連

**setup_acquisition(nframes=100)**

* リングバッファを確保して撮像条件を準備します。
* `nframes` はバッファ内に保持できるフレーム数（デフォルト 100）。

__start_acquisition(*args, **kwargs)__

* 撮像を開始します。
* `setup_acquisition()` がまだ呼ばれていなければ、ここで指定したパラメータを使って自動的にセットアップします。
* 既存設定と異なるパラメータが与えられた場合は、いったん取得をリセットして再設定されます。

**stop_acquisition()**

* 撮像を停止します。

**acquisition_in_progress()**

* 現在撮像中かどうかを真偽値で返します。

---

### フレーム書き出し

**read_newest_image(peek=False, return_info=False)**

* バッファ内で「最新の未読フレーム」を NumPy 配列として取得します。
* `peek=True`：読み出し済みフラグを進めない（再取得可能）。
* `return_info=True` の場合、`(frame, info)` を返し、`info` は uc480 向けの `TFrameInfo`

  * `frame_index, framestamp, timestamp, timestamp_dev, size, io_status, flags` を含みます。

**read_oldest_image(peek=False, return_info=False)**

* 「最も古い未読フレーム」を NumPy 配列として取得します。
* `peek`, `return_info` の挙動は `read_newest_image()` と同様です。

**read_multiple_images(rng=None, peek=False, missing_frame='skip', return_info=False, return_rng=False)**

* 複数フレームを一括で取得します。
* `rng=(first, last)`：読み出したいフレーム番号範囲（`first` 含む）。指定しない場合は「未読のすべて」。
* `peek=True`：未読フラグを進めずに読み出し。
* `missing_frame`：

  * `"skip"`：欠損フレームは戻り値からも削除。
  * `"none"`：該当位置を `None` で埋める。
  * `"zero"`：ゼロ配列で埋める。
* `return_info=True`：

  * `TFrameInfo` のリストとして、各フレームの `frame_index, framestamp, timestamp(実時間), timestamp_dev(カメラ起動からの時間), size, io_status, flags` が得られます。
* `return_rng=True`：

  * 実際にカバーするフレーム番号範囲を追加で返します（欠損処理の結果、`rng` より狭くなることがあります）。

**wait_for_frame(since='lastread', nframes=1, timeout=20.0)**

* 新しいフレームが到着するまで待機します。
* `error_on_stopped` はTLCamera版にもある共通の引数で、

  * 取得停止中に呼び出した際の挙動（例外を投げるか、単に False を返すか）を制御します。
* フレーム待機のタイムアウトでは `cam.TimeoutError` が発生します。タイムアウト時にFalseを返す関数ではありません。

---



## 4. サンプルコード (uc480 系)

### 4.1 画像を一枚撮影して保存
- 撮影画像は指定フォルダにpngとして保存されます。


```python
# ---- 設定 ----
EXPORT_PATH = "captured_image.png"  # PNG画像の保存パス
exposure = 0.01                     # 露光時間 [s]（10 ms）
gain = 1.0                          # マスターゲイン係数（dBではない）

from pathlib import Path
from PIL import Image
from pylablib.devices import uc480

cam_infos = uc480.list_cameras(backend="uc480")
if not cam_infos:
    raise RuntimeError("Thorlabs DCC/DCx カメラが接続されていません。")

Path(EXPORT_PATH).parent.mkdir(parents=True, exist_ok=True)
with uc480.UC480Camera(dev_id=cam_infos[0].dev_id, backend="uc480") as cam:
    cam.set_gains(master=gain)
    cam.set_exposure(exposure)  # PyLabLibは秒単位。SDK内のmsへの変換は不要
    cam.start_acquisition()
    cam.wait_for_frame(since="lastread", timeout=5.0, error_on_stopped=True)
    frame = cam.read_oldest_image()
    if frame is None:
        raise RuntimeError("画像を取得できませんでした。")
    Image.fromarray(frame).save(EXPORT_PATH)

print(f"画像を保存しました: {EXPORT_PATH}")
Image.open(EXPORT_PATH).show()

```

### 4.2 動画を10秒間撮影し保存

- 撮影動画は指定フォルダにmp4として保存されます。
- 各フレームの機器時刻を `time_stamps` に保持します。動画ファイルへの時刻の埋め込み・別ファイルへの保存は行いません。
- 固定fpsの表示用動画です。フレーム欠損や取得間隔の変動は動画の再生時間には反映されません。取得画像をメモリに保持するため、長時間の記録には向きません。


```python
# ---- 設定 ----
t = 10.0
EXPORT_PATH = "captured_video.mp4"
exposure = 0.01     # 露光時間 [s]
fps = 30.0         # 希望フレームレート [frames/s]
gain = 1.0         # マスターゲイン係数

from pathlib import Path
import time
import datetime
import cv2
from pylablib.devices import uc480

frames = []
time_stamps = []
cam_infos = uc480.list_cameras(backend="uc480")
if not cam_infos:
    raise RuntimeError("Thorlabs DCC/DCx カメラが接続されていません。")

with uc480.UC480Camera(dev_id=cam_infos[0].dev_id, backend="uc480") as cam:
    cam.set_color_mode("mono8")  # この動画例は8-bitのモノクロ画像を使用
    cam.set_exposure(exposure)
    cam.set_gains(master=gain)
    cam.set_frame_period(1.0 / fps)
    fps = 1.0 / cam.get_frame_period()  # 実際に設定された周期を動画のfpsに使用
    cam.setup_acquisition(nframes=200)
    cam.start_acquisition()
    start_time = time.perf_counter()
    while time.perf_counter() - start_time < t:
        images, infos = cam.read_multiple_images(return_info=True)
        for img, finfo in zip(images, infos):
            ts = finfo.timestamp
            time_stamps.append(datetime.datetime(
                ts.year, ts.month, ts.day, ts.hour, ts.minute,
                ts.second, ts.millisecond * 1000,
            ))
            frames.append(img)
        time.sleep(0.01)

if not frames:
    raise RuntimeError("フレームを取得できませんでした。")
Path(EXPORT_PATH).parent.mkdir(parents=True, exist_ok=True)
height, width = frames[0].shape[:2]
writer = cv2.VideoWriter(
    EXPORT_PATH, cv2.VideoWriter_fourcc(*"mp4v"), fps,
    (width, height), isColor=False,
)
try:
    if not writer.isOpened():
        raise RuntimeError("動画の保存先・コーデックを確認してください。")
    for frame in frames:
        writer.write(frame)
finally:
    writer.release()
print(f"動画を保存しました: {EXPORT_PATH}")

```

---

## 5. よく使用するメソッド一覧（ThorlabsTLCamera）

### 初期化・クローズ

**open()**

* カメラへの接続を開きます。
* 通常はコンストラクタ `ThorlabsTLCamera(serial)` で自動的に開くので、明示的に呼ぶ場面は多くありません。

**close()**

* カメラとの接続を閉じ、内部のリソースを解放します。
* スクリプト終了前や、`try/finally` で必ず呼ぶようにしておくと安全です。
* with文を使うとスクリプト終了や例外発生時に自動的に呼ばれるため安全です。

**is_opened()**

* デバイスが現在接続されているかどうかを真偽値で返します。
* 多重クローズを避けたい時や、再接続処理を行う前のチェックに便利です。

---

### 情報の取得

**get_device_info()**

* モデル名・表示名・シリアル番号・ファームウェアバージョンを取得します。
* 返り値は `(model, name, serial_number, firmware_version)` というタプルです。

**get_detector_size()**

* センサー全体の画素数を `(width, height)` のタプルで返します。
* ROI 設定や保存画像サイズの決定に使います。

---

### パラメータの取得・設定

**get_exposure() / set_exposure(exposure)**

* 露光時間（秒）を取得・設定します。

**get_gain() / set_gain(gain, truncate=True)**

* カメラのゲイン値（dB）を取得・設定します。
* `truncate=True`：範囲外の値を自動的に許容範囲内にクリップ。
* `truncate=False`：範囲外を指定すると例外が送出されます。

---

### 撮影関連

**setup_acquisition(nframes=100)**

* 撮像したフレームを保持するリングバッファを確保します。
* `nframes` はバッファに溜められる最大フレーム数で、高フレームレート・長時間記録では大きめに取っておくとフレーム欠損を減らせます。

**start_acquisition(frames_per_trigger='default', auto_start=True, nframes=None)**

* カメラの連続取得を開始します。
* `frames_per_trigger`：

  * `None`：停止するまで無制限に取得（ソフトウェアトリガ時のデフォルト）。
  * ハードウェアトリガではデフォルト 1（パルス1回につき1フレーム）。
* `auto_start=True`：

  * トリガモードが `"int"`（ソフトウェア）であれば、自動で撮像開始。
  * `False` にしておくと `send_software_trigger()` を呼ぶまで実際の取得は始まりません。
* `nframes`：リングバッファのサイズ。`setup_acquisition()` を明示的に呼ばない場合にここで指定できます。

**stop_acquisition()**

* 撮像を停止します。

**acquisition_in_progress()**

* 現在撮像中かどうかを真偽値で返します。

---

### フレーム書き出し

**read_newest_image(peek=False, return_info=False)**

* バッファ内で「最新の未読フレーム」を NumPy 配列として取得します。
* `peek=True` の場合、フレームを「読んだことにしない」ので、後から同じフレームを再取得できます。
* `return_info=True` の場合、`(frame, info)` のタプルを返し、`info` は `TFrameInfo`（`frame_index, framestamp, pixelclock, pixeltype, offset` を持つ）です。

**read_oldest_image(peek=False, return_info=False)**

* 「最も古い未読フレーム」を NumPy 配列として取得します。
* 他の挙動（`peek`, `return_info`）は `read_newest_image()` と同様です。

**read_multiple_images(rng=None, peek=False, missing_frame='skip', return_info=False, return_rng=False)**

* 複数フレームをまとめて読み出します。
* `rng=(first, last)` でフレーム番号レンジ（`first` 含む）を指定。`None` の場合は「未読の全フレーム」を読み出します。
* `peek=True`：読み出し位置を進めずに中身だけ取得。
* `missing_frame`：

  * `"skip"`：欠損フレームは完全にスキップ（戻り値の配列からも消える）。
  * `"none"`：該当位置に `None` を入れる。
  * `"zero"`：同サイズのゼロ配列を入れる。
* `return_info=True`：`(frames, infos)` を返し、`infos` は各フレームの `TFrameInfo`。
* `return_rng=True`：実際に返されたフレームがカバーしているフレーム番号の範囲を追加で返します。

**wait_for_frame(since='lastread', nframes=1, timeout=20.0)**

* 指定条件を満たす新しいフレームが来るまで待機します。
* `since`：

  * `'lastread'`（デフォルト）：最後に読み取ったフレームから数えて `nframes` 枚分が到着するまで待機。
  * `'lastwait'`：直前の `wait_for_frame()` 呼び出し時点からカウント。
  * `'now'`：この関数を呼んだ時刻を起点にカウント。
  * `'start'`：撮像開始時点から `nframes` 枚溜まるまで待つ。
* `timeout`：タイムアウト時間（秒）。`None` なら無制限。

**get_timestamp_clock_frequency()**

* フレームタイムスタンプのカウンタクロック周波数（Hz）を返します。
* `TFrameInfo.pixelclock` をこの周波数で割ると、カメラのクロックを秒単位へ変換できます。`framestamp` はフレーム番号で、時刻のカウンタではありません。

---


## 6. サンプルコード(TLCamera 系)

### 6.1 画像を一枚撮影して保存
- 撮影画像は指定フォルダにpngとして保存されます。


```python
# ---- 設定 ----
EXPORT_PATH = "captured_image.png"
exposure = 0.01  # 露光時間 [s]
gain = 0.0       # TLCameraのゲイン [dB]

from pathlib import Path
from PIL import Image
from pylablib.devices.Thorlabs.TLCamera import list_cameras, ThorlabsTLCamera

serial_list = list_cameras()
if not serial_list:
    raise RuntimeError("Thorlabsカメラが接続されていません。")

Path(EXPORT_PATH).parent.mkdir(parents=True, exist_ok=True)
with ThorlabsTLCamera(serial=serial_list[0]) as cam:
    cam.set_exposure(exposure)
    cam.set_gain(gain)
    cam.set_trigger_mode("int")
    cam.start_acquisition()
    cam.wait_for_frame(since="lastread", timeout=5.0, error_on_stopped=True)
    frame = cam.read_oldest_image()
    if frame is None:
        raise RuntimeError("画像を取得できませんでした。")
    Image.fromarray(frame).save(EXPORT_PATH)

print(f"画像を保存しました: {EXPORT_PATH}")
Image.open(EXPORT_PATH).show()

```

### 6.2 動画を10秒間撮影し保存

- 撮影動画は指定フォルダにmp4として保存されます。
- 各フレームの機器時刻を `time_stamps` に保持します。動画ファイルへの時刻の埋め込み・別ファイルへの保存は行いません。
- 固定fpsの表示用動画です。フレーム欠損や取得間隔の変動は動画の再生時間には反映されません。取得画像をメモリに保持するため、長時間の記録には向きません。


```python
# ---- 設定 ----
t = 10.0
EXPORT_PATH = "captured_video.mp4"
exposure = 0.01  # 露光時間 [s]
gain = 0.0       # TLCameraのゲイン [dB]
fps = 30.0

from pathlib import Path
import time
import numpy as np
import cv2
from pylablib.devices.Thorlabs.TLCamera import list_cameras, ThorlabsTLCamera

serial_list = list_cameras()
if not serial_list:
    raise RuntimeError("Thorlabsカメラが接続されていません。")
frames = []
time_stamps = []

with ThorlabsTLCamera(serial=serial_list[0]) as cam:
    cam.set_exposure(exposure)
    cam.set_gain(gain)
    cam.set_trigger_mode("int")
    cam.set_frame_period(1.0 / fps)
    fps = 1.0 / cam.get_frame_period()
    bit_depth = cam.get_sensor_info().bit_depth
    ts_freq = cam.get_timestamp_clock_frequency()
    cam.setup_acquisition(nframes=200)
    cam.start_acquisition(frames_per_trigger=None, auto_start=False)
    start_time = time.perf_counter()
    cam.send_software_trigger()
    while time.perf_counter() - start_time < t:
        images, infos = cam.read_multiple_images(return_info=True)
        for img, finfo in zip(images, infos):
            # TLCameraにはtimestamp属性がない。pixelclockから相対的な機器時刻を取得
            time_stamps.append(
                finfo.pixelclock / ts_freq
                if finfo is not None and finfo.pixelclock is not None and ts_freq
                else None
            )
            frames.append(img)
        time.sleep(0.01)

if not frames:
    raise RuntimeError("フレームを取得できませんでした。")
Path(EXPORT_PATH).parent.mkdir(parents=True, exist_ok=True)
height, width = frames[0].shape[:2]
is_color = frames[0].ndim == 3 and frames[0].shape[2] == 3
writer = cv2.VideoWriter(
    EXPORT_PATH, cv2.VideoWriter_fourcc(*"mp4v"), fps,
    (width, height), isColor=is_color,
)
try:
    if not writer.isOpened():
        raise RuntimeError("動画の保存先・コーデックを確認してください。")
    for frame in frames:
        # 表示用動画はセンサーのbit深度に合わせて8-bitへ変換。元配列はframesに保持
        if frame.dtype != np.uint8:
            frame = np.clip(frame / float((1 << bit_depth) - 1) * 255, 0, 255).astype(np.uint8)
        if is_color:
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        writer.write(frame)
finally:
    writer.release()
print(f"動画を保存しました: {EXPORT_PATH}")

```

---

### `npy_to_image(npy_path, out_path):`
- npyファイルを画像ファイルに変換して保存します。


```python
def npy_to_image(npy_path, out_path):
    """
    指定ディレクトリ内の .npy 画像ファイルを読み込み、画像ファイルとして保存します。

    Parameters
    ----------
    npy_path : str
        .npy 画像ファイルのパス。   
    out_path : str
        変換後の画像ファイルのパス
    """
    import os
    import numpy as np
    from PIL import Image

    # .npy ファイルを読み込み
    img_array = np.load(npy_path)

    # NumPy 配列を画像に変換
    img = Image.fromarray(img_array)

    # 画像ファイルとして保存
    img.save(out_path)
```

### `npy_to_video(npy_dir, out_path, fps=30):`
- npyファイル群を動画ファイルに変換して保存します。
- 以下は表示用の変換例。各フレームの最小値・最大値で正規化するため、元の画素値やフレーム間の強度比は保持しません。


```python
import os
import glob
import numpy as np
import cv2

def npy_to_video(npy_dir, out_path, fps=30):
    """指定フォルダ内の npy ファイルを動画ファイルに変換して保存する

    Args:
        npy_dir (str): npyファイルが保存されているディレクトリのパス
        out_path (str): 変換後の動画ファイルのパス（例: output.mp4）
        fps (int): 出力動画のフレームレート

    Returns:
        return (None): なし
    """
    # --- 1. npyファイル一覧を取得し、ファイル名（=経過時間）でソート ---
    pattern = os.path.join(npy_dir, "*.npy")
    files = glob.glob(pattern)

    # ファイル名（拡張子除く）を float として扱い、その値でソート
    time_and_path = []
    for f in files:
        base = os.path.basename(f)
        name, _ = os.path.splitext(base)
        try:
            t = float(name)  # 撮影開始からの経過時間 [s]
        except ValueError:
            # 秒数として解釈できないファイル名は無視
            continue
        time_and_path.append((t, f))

    if not time_and_path:
        raise RuntimeError("有効な npy ファイルが見つかりませんでした。")

    time_and_path.sort(key=lambda x: x[0])

    # --- 2. 最初のフレームを読み込み、サイズと形状を確認 ---
    first_time, first_path = time_and_path[0]
    first_frame = np.load(first_path)

    # 画素値を 0–255 の uint8 に正規化
    frame_u8 = _normalize_to_uint8(first_frame)

    # 2D の場合はグレースケール → BGR 3ch に変換
    if frame_u8.ndim == 2:
        frame_bgr = cv2.cvtColor(frame_u8, cv2.COLOR_GRAY2BGR)
    elif frame_u8.ndim == 3 and frame_u8.shape[2] == 3:
        frame_bgr = frame_u8
    else:
        raise ValueError(f"サポートされていない画像形状です: {first_frame.shape}")

    height, width, _ = frame_bgr.shape

    # --- 3. 動画ライターを作成 ---
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    writer = cv2.VideoWriter(out_path, fourcc, fps, (width, height))
    if not writer.isOpened():
        writer.release()
        raise RuntimeError("動画の保存先・コーデックを確認してください。")

    # --- 4. 全フレームを書き込む ---
    try:
        # 先頭フレーム
        writer.write(frame_bgr)

        # 残りのフレーム
        for t, path in time_and_path[1:]:
            frame = np.load(path)
            frame_u8 = _normalize_to_uint8(frame)

            if frame_u8.ndim == 2:
                frame_bgr = cv2.cvtColor(frame_u8, cv2.COLOR_GRAY2BGR)
            elif frame_u8.ndim == 3 and frame_u8.shape[2] == 3:
                frame_bgr = frame_u8
            else:
                # 形状が合わないフレームはスキップ
                print(f"警告: 形状が異なるためスキップします: {path}, shape={frame.shape}")
                continue

            if frame_bgr.shape != (height, width, 3):
                print(f"警告: 画像サイズが異なるためスキップします: {path}")
                continue
            writer.write(frame_bgr)
    finally:
        writer.release()


def _normalize_to_uint8(frame):
    """任意の型・レンジの画像を 0–255 の uint8 に正規化する

    Args:
        frame (np.ndarray): 入力画像（2D または 3D の NumPy 配列）

    Returns:
        return (np.ndarray): uint8 型に正規化された画像
    """
    if frame.dtype == np.uint8:
        return frame

    frame = frame.astype(np.float32)
    vmin = float(np.min(frame))
    vmax = float(np.max(frame))

    if vmax <= vmin:
        # 画素値が一定の場合
        return np.zeros_like(frame, dtype=np.uint8)

    frame = (frame - vmin) / (vmax - vmin) * 255.0
    return frame.astype(np.uint8)

```
