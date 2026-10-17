# Optical Power Monitor (OPM) をPythonで操作する

Windowsでパワーメーターを操作するための導入メモ。PyLabLib 1.4.5のAPI・ソースと照合した操作例を記載する。
別の自作ソフトでは実機の取得・実験に使用しているが、本プロジェクトの実機ドライバーは未実装。

## 1. Optical Power Monitorのインストール

以下の公式サイトを開き、「Software」→「Download」でソフトをダウンロード。  
[公式サイト](https://www.thorlabs.com/software-pages/OPM)

OPM用のインターフェイスと、対象機器用のドライバーをインストールする。  
通常はPCとOPMをUSBで接続し、OPM用のインターフェイスからパワーを表示・保存する。  
PyLabLibの `PM160` はNI-VISA経由で通信する。ThorlabsのSDKのDLLを直接呼ぶ方法とは異なる。

## 2. NI VISA Runtime のインストール

NI-VISAは、計測器制御の標準規格VISAをNIが実装したドライバー・API。  
ダウンロード時にNIのアカウントを求められる場合がある。

手順：  
[公式サイト](https://www.ni.com/ja/support/downloads/drivers/download.ni-visa.html)にアクセスし、使用するWindowsに対応したNI-VISA Runtimeをダウンロード。ログインを求められた場合はNIのアカウントを作成する。

## 3. OPM制御用のPythonライブラリをインストール
### PyLabLibのインストール
PyLabLibは様々な実験デバイスのSDKをラッパーし、それらのデバイスをPythonから制御したり自動化したりするためのライブラリ。  
Thorlabsのデバイスは `pylablib.devices.Thorlabs` モジュールから制御できる（[参考記事](https://qiita.com/h1na/items/d305d49b5a27e92d132a)）。

### pyvisaのインストール
PyVISAは、PythonからVISA（Virtual Instrument Software Architecture）を使用して計測器を制御するためのライブラリ。  
PyLabLibの内部で使用されているため、PyLabLibをインストールすると自動的にインストールされます。


```powershell
python -m pip install "pylablib==1.4.5" matplotlib
```

NumPy・pandas・PyVISAはPyLabLibの依存として導入される。matplotlibは後半のプロット用。

PyLabLibのライセンスはGPLv3。PyVISAはMIT Licenseだが、PyLabLibと結合したプログラムを再配布する場合はGPLv3の条件に従う必要がある。
OPM用ソフトウェアやNI-VISA Runtimeには各提供元の利用条件が適用される。詳細は [依存ライブラリとライセンス](../THIRD_PARTY_NOTICES.md) を参照。

## 4. PylabLibを使ってOPMを制御

### 4.1 VISA アドレスを確認

pyvisaでOPMのVISAアドレスを確認。

- "USB" を含み、
- "0x1313"（Thorlabs の Vendor ID）を含む

ようなアドレスが候補になる（例：USB0::0x1313::0x8078::SAMPLE::INSTR）。この条件だけで機種は特定できないため、表示されたアドレスと対象機器を確認して選択する。

```python
import pyvisa

with pyvisa.ResourceManager() as rm:
    addr_list = rm.list_resources()
if not addr_list:
    raise Exception("接続されたデバイスが見つかりません。")
print(addr_list)
```

#### 表示されない場合

`rm.list_resources()` が空 `()` のときは、パワーメーターの入力がWindows側でVISA(USBTMC)として認識されていない可能性がある。

1. **PyVISAがNI-VISAを使っているか確認**

   - `rm.visalib` のパスがNI-VISAのインストール先を指しているか確認。DLLのファイル名だけでは提供元を判別できない。

     ```python
     import pyvisa

     rm = pyvisa.ResourceManager()
     print("VISA library:", rm.visalib)        # どのVISAを使っているか
     print("Resources:", rm.list_resources())  # 一覧
     print("USB only:", rm.list_resources("USB?*::INSTR"))
     print("ASRL only:", rm.list_resources("ASRL?*::INSTR"))
      rm.close()
     ```

2. **デバイスマネージャーで認識状態を確認**

   - デバイスマネージャーを開き、OPMが「測定器」や「USB機器」として認識されているか確認。
   - 「不明なデバイス」や「その他のデバイス」になっている場合、ドライバが正しく当たっていない可能性があります。
   - 認識されている場合は、次に進みOPMがNI-VISA(USBTMC)として認識されているか確認。

3. **NI-MAX（Measurement & Automation Explorer）で見えるか確認**

   - NI-MAX 側に機器が出ない場合、OSがOPMの入力をNI-VISA(USBTMC)として掴めていない。

4. **Power Meter Driver SwitcherでVISA用のドライバーに切り替える**

   - OPM付属の **Power Meter Driver Switcher** を起動し、対象機器をVISA（USBTMC）で使う設定に切り替える。
   - スタートメニューから **Driver Switcher** を検索して起動。
   - PyLabLibの[公式導入資料](https://pylablib.readthedocs.io/en/stable/devices/Thorlabs_pm.html)では **PM100Dモード** と記載。機種名がPM160でも、この名称を使う。画面の項目名はOPMのバージョンによって確認する。
   * 切り替え後に **USB抜き差し**（必要ならPC再起動）。
   * これで `USB0::0x1313::...::INSTR` のような形式で `list_resources()` に出るようになる。

5. **それでもダメな場合**

   * OPM（＋SDK）と NI-VISA Runtime を **修復インストール / 再インストール**。
   * USBハブを避けて直結、別USBポートで試す。
   * 以前は認識できていた場合も、使用中のドライバーとDriver Switcherの設定を再確認する。




## 4.2 PM160 クラス

**PM160(addr)** [ドキュメント](https://pylablib.readthedocs.io/en/stable/.apidoc/pylablib.devices.Thorlabs.html#pylablib.devices.Thorlabs.misc.PM160)
- Thorlabs OPM PM160/PM100Aを制御するクラス
- `addr` : VISAアドレス（文字列）


```python
from pylablib.devices.Thorlabs.misc import PM160

addr = addr_list[0]  # 一覧で対象機器を確認し、複数ある場合は選択を変更する
pm = PM160(addr)
pm.close()  # デバイスを閉じる
```

## 4.3 よく使うメソッド・プロパティ

### 1. 初期化・クローズ

**open()**

* デバイスとの通信を開きます。
* 通常は `Thorlabs.PM160(addr)` のコンストラクタで自動的に開くので、明示的に呼ぶ場面は少なめです。

**close()**

* デバイスとの接続を閉じ、リソースを解放します。
* `with` 文を使えば自動で呼ばれますが、手動で使う場合はスクリプトの最後に必ず `close()` しておくのが安全です。

**is_opened()**

* デバイスが現在接続されているかどうかを真偽値で返します。
* エラー処理や再接続処理の前に状態チェックするのに便利です。

---

### 2. デバイス・センサ情報

**get_device_info()**

* 本体側の情報（モデル名・シリアル・ファームウェアバージョンなど）を取得します。

**get_sensor_info()**

* センサーヘッドの情報（型番・シリアル・タイプなど）を取得します。

**get_supported_sensor_modes()**

* このセンサでサポートされている測定モード一覧を返します（例：`"power"`, `"energy"` 等）。

**get_full_info() / get_settings()**

* デバイス・センサに関する情報や現在の設定をまとめて取得します。
* デバッグ時や、ログとして条件を残しておきたいときに便利です。

---

### 3. 測定モード・レンジ設定

#### 測定モード（Power / Energy など）

**get_sensor_mode() / set_sensor_mode(mode)**

* 現在のセンサモードを取得・設定します。
* 典型的には CW パワー測定なら `"power"` を指定します。

```python
pm.set_sensor_mode("power")   # パワーモードに設定
```

---

#### レンジ／オートレンジ

**is_autorange_enabled() / enable_autorange(enabled=True)**

* オートレンジ機能の ON/OFF を取得・設定します。

**get_range() / set_range(rng)**

* 現在の手動レンジ値を取得・設定します。
* 通常は `enable_autorange(True)` で自動にしておき、必要なときだけ固定レンジにする、という使い方が多いと思います。

---

### 4. 波長設定

**get_wavelength() / set_wavelength(wavelength)**

* 現在設定されている波長を **m単位**で取得・設定します。
* センサーの波長補正に使用するため、実験の中心波長（例：800 nm）をここで指定します。
* PyLabLib 1.4.5の[ソース](https://pylablib.readthedocs.io/en/stable/_modules/pylablib/devices/Thorlabs/misc.html#GenericPM.set_wavelength)では、渡した値を `1e9` 倍して機器へ送ります。APIの説明文にある「nm」と実装が異なるため、以下では実装に合わせてm単位を使います。

```python
pm.set_wavelength(800e-9)  # 測定波長を800 nmに設定
```

**get_wavelength_range()**

* このセンサで設定可能な波長範囲（下限・上限）をm単位で返します。
  → 「この波長を指定できるのか？」をコード側でチェックしたいときに使えます。

---

### 5. 測定値の取得

#### 5.1 一回の測定（新たに測定する）

**get_power()**

* 現在のパワー値を **W 単位**で返します。

```python
p = pm.get_power()  # [W]
```

#### 5.2 ディスプレイ表示値の取得

**get_reading(sensor_mode=None, measure=True, overrng="keep")**

* より汎用的な測定値取得メソッドです。
* 引数 `measure` によって挙動が変わります（ドキュメントより）：

  * `measure=True`（デフォルト）：新たに測定を行い、その結果を返す。
  * `measure=False`：最後に測定された値を取得する。
* `get_power()` は測定モードを `"power"` に指定して新たに測定するメソッドです。

---

## 6. サンプルコード

### 6.1 現在のパワーを取得


```python
# --- 設定 ---
wavelength = 785  # nm, 測定波長を設定


from pylablib.devices import Thorlabs
import pyvisa

# デバイスのアドレスを取得
with pyvisa.ResourceManager() as rm:
    addr_list = rm.list_resources()
if not addr_list:
    raise Exception("接続されたデバイスが見つかりません。")
print(addr_list)
addr = addr_list[0]  # 接続されたデバイスのアドレスを使用。複数ある場合は適宜選択してください。

# PM160デバイスのインスタンスを作成
# 設定に失敗した場合も、with文で接続を閉じる
with Thorlabs.PM160(addr) as pm:
    pm.set_sensor_mode("power")
    pm.set_wavelength(wavelength * 1e-9)  # 測定波長 (nm -> m)
    print("測定開始")
    # 現在のパワーを取得
    power = pm.get_power()  # [W]
    print(f"現在のパワー: {power*1000} mW")
print("測定終了")
```

### 6.2 ディスプレイ表示値をループで取得し、CSVに保存、プロット
- 測定時間、測定間隔、保存ファイル名を指定して実行します。
- 測定・保存の処理が挟まるため、厳密には測定間隔通りではありません


```python
# --- 設定 ---
t = 10              # 測定時間 [s]
dt = 0.5            # 測定間隔 [s]
wavelength = 785    # nm, 測定波長を設定
CSV_FILENAME = "data\\power_measurement.csv"


import time
import pandas as pd
from pylablib.devices import Thorlabs
import pyvisa
import os
import matplotlib.pyplot as plt

# CSVファイルのディレクトリを作成
os.makedirs(os.path.dirname(CSV_FILENAME) or ".", exist_ok=True)
# CSVファイルのヘッダーを作成（ファイルが存在しない場合のみ）
if not os.path.exists(CSV_FILENAME):
    df_header = pd.DataFrame(columns=['Time [s]', 'Power [mW]'])
    df_header.to_csv(CSV_FILENAME, index=False)

# このコードだけでも実行できるよう、対象機器のアドレスを取得
with pyvisa.ResourceManager() as rm:
    addr_list = rm.list_resources()
if not addr_list:
    raise RuntimeError("接続されたデバイスが見つかりません。")
print(addr_list)
addr = addr_list[0]  # 一覧で対象機器を確認し、複数ある場合は選択を変更する

# 測定
print("測定開始")
with Thorlabs.PM160(addr) as pm:
    pm.set_sensor_mode("power")
    pm.set_wavelength(wavelength * 1e-9)  # 測定波長 (nm -> m)
    start_time = time.perf_counter()
    while (time.perf_counter() - start_time) < t:
        elapsed_time = time.perf_counter() - start_time # 経過時間 [s]
        power = pm.get_reading(measure=False)  # 最後の測定値 [W]
        
        # データを即座にCSVに追記
        df_row = pd.DataFrame({
            'Time [s]': [elapsed_time],
            'Power [mW]': [power * 1000]
        })
        df_row.to_csv(CSV_FILENAME, mode='a', header=False, index=False)
        
        print(f"Time: {elapsed_time:.2f} s, \tPower: {power*1000:.4f} mW")
        time.sleep(dt)
print(f"測定終了 - データを {CSV_FILENAME} に保存しました")

# データのプロット
data = pd.read_csv(CSV_FILENAME)    
plt.figure(figsize=(8,5))
plt.plot(data['Time [s]'], data['Power [mW]'], marker='o')
plt.title('Power Measurement Over Time')    
plt.xlabel('Time [s]')
plt.ylabel('Power [mW]')
plt.grid()
plt.show()
```
