r"""
API経由でのデバイスの探索・接続・切断を確認する。


## メモ
TestClientで実際にサーバーを起動することなくAPIのテストができる

```
app = FastAPI()
clinet = TestClient(app)
```

withを使用することでappの初期化処理と停止処理が正しく行われる

メソッド
client.get()
client.post()
client.put()
client.patch()
client.delete()

レスポンスに対して次の確認ができる
response.status_code   # HTTPステータスコード
response.json()        # JSON形式のレスポンスを辞書などに変換
response.text          # レスポンス本文を文字列として取得
response.headers       # レスポンスヘッダー

クエリやJSONを渡せる
```
# クエリパラメータ
response = client.get("/api/data", params={"limit": 100})

# JSONデータ
response = client.post(
    "/api/settings",
    json={"sampling_interval": 0.1}
)
```

コードの実行
```
.\.venv\Scripts\python.exe -B -m backend.tests.test_measurement_api
```
"""

from fastapi.testclient import TestClient
import json
import time
from datetime import datetime
from pathlib import Path

from backend.app.models.devices import PowerMeterSettings
from backend.app.models.recordings import RecordingStatus, StartRecordingRequest

from backend.app.main import app

def print_json(data: object) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))

# ======================= status =======================

def test_status(client: TestClient):
    """アプリの状態を確認する。"""
    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    print(f"状態取得:\t{response.json()}")


# ======================= devices =======================

def test_scan_devices(client: TestClient) -> None:
    """デバイスをスキャンして登録する。"""
    response = client.post("/api/devices/scan")

    assert response.status_code == 200

    print("スキャン結果")
    print_json(response.json())

def test_get_devices(client: TestClient):
    """登録済みのデバイスを取得する"""
    response = client.get("/api/devices")

    assert response.status_code == 200

    print("デバイス一覧")
    print_json(response.json())

def test_get_device_info(client: TestClient, id: str):
    """特定のデバイスを取得する"""
    response = client.get(f"/api/devices/{id}")

    assert response.status_code == 200

    print("デバイス情報")
    print_json(response.json())

def test_connect_device(client: TestClient, id: str):
    """デバイスに接続する"""
    response = client.post(f"/api/devices/{id}/connect")

    assert response.status_code == 200
    assert response.json() == {"success": True}

    print("接続")
    print_json(response.json())

def test_disconnect_device(client: TestClient, id: str):
    """デバイスとの接続を切断する"""
    response = client.post(f"/api/devices/{id}/disconnect")

    assert response.status_code == 200
    assert response.json() == {"success": True}

    print("切断")
    print_json(response.json())

def test_get_settings(client: TestClient, id: str):
    """デバイスの設定を取得する"""
    response = client.get(f"/api/devices/{id}/settings")

    assert response.status_code == 200
    assert isinstance(response.json(), dict)

    print("デバイス設定")
    print_json(response.json())

def test_update_settings(client: TestClient, id: str):
    """現在の設定を取得し、波長を変更して設定一式を送信する。"""
    path = f"/api/devices/{id}/settings"

    # 現在の設定一式を辞書で取得する。
    response = client.get(path)
    assert response.status_code == 200, response.text
    new_settings = response.json()

    # 取得した設定の波長を書き換える。
    new_settings["wavelength"] = 623.0

    # 設定一式を送信し、適用後の設定を確認する。
    response = client.put(path, json=new_settings)
    assert response.status_code == 200, response.text
    assert response.json() == new_settings

    print("デバイス設定更新")
    print_json(response.json())

def test_start_sampling(client: TestClient, id: str):
    """サンプリングを開始する"""
    response = client.post(f"/api/devices/{id}/sampling/start")

    assert response.status_code == 200
    assert response.json() == {"success": True}

    print("サンプリング開始")
    print_json(response.json())

def test_stop_sampling(client: TestClient, id: str):
    """サンプリングを停止する"""
    response = client.post(f"/api/devices/{id}/sampling/stop")

    assert response.status_code == 200
    assert response.json() == {"success": True}

    print("サンプリング停止")
    print_json(response.json())

def test_is_sampling(client: TestClient, id: str):
    """サンプリング中か確認する"""
    response = client.get(f"/api/devices/{id}/sampling")

    assert response.status_code == 200
    assert isinstance(response.json()["is_sampling"], bool)

    print("サンプリング中か確認")
    print_json(response.json())


# ======================= recordings =======================


def test_start_recording(client: TestClient):
    """保存を開始する"""

    # 記録設定
    body = StartRecordingRequest(
        directory=Path(__file__).parent / "output" / "recordings",
        name=f"test_{datetime.now():%Y%m%d_%H%M%S_%f}",
    )
    response = client.post(
        f"/api/recordings/start",
        json=body.model_dump(mode="json")
    )

    assert response.status_code == 200

    print("記録の開始")
    print_json(response.json())

def test_stop_recording(client: TestClient):
    """保存を停止する"""
    response = client.post(f"/api/recordings/stop", )

    assert response.status_code == 200

    print("記録の停止")
    print_json(response.json())

def test_recording_status(client: TestClient):
    """記録状態を取得"""
    response = client.get(f"/api/recordings/status")

    assert response.status_code == 200

    print("記録状態を取得")
    print_json(response.json())



# =======================================================


def main() -> None:
    id = "DummyPowerMeter_DUMMY123456"
    id2 = "DummyPowerMeter_DUMMY123457"

    with TestClient(app) as client:
        # 接続の確認
        test_status(client)

        # デバイスのスキャンと取得
        test_scan_devices(client)
        test_get_devices(client)

        # デバイス情報と設定
        test_get_device_info(client, id)
        test_get_settings(client, id)

        # デバイスの接続
        test_connect_device(client, id)
        test_connect_device(client, id2)

        # サンプリングの開始
        test_start_sampling(client, id)
        test_start_sampling(client, id2)

        # 記録の開始・状態確認・停止
        test_start_recording(client)        # 開始
        time.sleep(2)
        test_recording_status(client)       # 記録中に状態を確認
        test_update_settings(client, id)    # 記録中に設定を変更
        test_update_settings(client, id2)   # 記録中に設定を変更
        time.sleep(1)
        test_stop_recording(client)         # 終了

        # サンプリングの停止と切断
        test_stop_sampling(client, id)
        test_stop_sampling(client, id2)
        test_disconnect_device(client, id)
        test_disconnect_device(client, id2)
        
        time.sleep(1)




if __name__ == "__main__":
    main()
