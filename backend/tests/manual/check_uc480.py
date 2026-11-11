r"""uc480の実機テスト。下の設定を編集し、引数なしで実行する。

リポジトリ直下から:
    .\.venv\Scripts\python.exe -m backend.tests.manual.check_uc480
"""

from pathlib import Path

from backend.app.devices.camera.Thorlabs_uc480 import UC480
from backend.tests.manual.hardware_check_common import (
    LOG,
    capture_cycle,
    finish_output,
    positive_seconds,
    prepare_output,
)


# ==================== ここを編集する ====================

# 最初はTrueで実行してシリアル番号を調べる。
# 接続テストを行うときはFalseに変更する。
SCAN_ONLY = False

# 探索結果のserial_numberに表示された値を貼り付ける。
CAMERA_SERIAL = "4102906151"

# 取得を2回行う。それぞれの取得時間を秒で指定する。
# 低FPS・長い露光でテストするときは、この値を増やす。
CAPTURE_SECONDS = 5

# このフォルダーの下へ、uc480/日時/としてログ・JSON・確認画像を保存する。
OUTPUT_ROOT = Path("test-results/hardware")


def main() -> int:
    """uc480の探索、または接続・設定・画像取得・切断を順に確認する。"""
    seconds = positive_seconds(CAPTURE_SECONDS)
    output = prepare_output("uc480", OUTPUT_ROOT)

    result = {"device": "uc480", "passed": False, "cycles": []}
    device = None
    stage = "探索"

    try:
        if SCAN_ONLY:
            # カメラは開かず、SDKが検出したモデル・シリアル番号・dev_idを表示する。
            result["discovered"] = [item.model_dump(mode="json") for item in UC480.scan()]
            LOG.info("カメラの探索結果: %s", result["discovered"])

        else:
            if not CAMERA_SERIAL:
                raise ValueError("ファイル先頭のCAMERA_SERIALを指定してください")

            # 1. シリアル番号から現在のdev_idを引き直し、カメラへ接続する。
            stage = "接続"
            device = UC480(device_id="hardware-uc480", serial_number=CAMERA_SERIAL)
            device.logger.addHandler(LOG.handlers[-1])

            if not device.connect() or not device.is_connected():
                raise RuntimeError("uc480の接続状態を確認できませんでした")

            # 2. カメラ情報と、実際の画像サイズ・FPS・露光などを記録する。
            stage = "機器情報・設定取得"
            result["info"] = device.get_info().model_dump(mode="json")
            result["initial_settings"] = device.get_settings().model_dump(mode="json")

            LOG.info("接続完了: %s", result["info"])
            LOG.info("現在の設定: %s", result["initial_settings"])

            # 3. 現在の設定を再適用し、設定書き込みと読み戻しを確認する。
            # 画像サイズや露光を任意の新しい値に変更するテストではない。
            stage = "停止中の設定再適用"
            applied = device.set_settings(device.get_settings())
            result["applied_settings"] = applied.model_dump(mode="json")

            # 4. 画像を取得し、最初の1枚をPNGへ保存する。
            # 途中で設定を再適用し、その後も画像が届くことを確認する。
            stage = "1回目の取得・停止"
            result["cycles"].append(capture_cycle(device, seconds, output, cycle=1))

            # 5. 停止後に再取得し、最初の1枚を別のPNGへ保存する。
            stage = "2回目の取得・停止"
            result["cycles"].append(capture_cycle(device, seconds, output, cycle=2))

        result["passed"] = True

    except Exception as exc:
        result["error"] = {"stage": stage, "type": type(exc).__name__, "message": str(exc)}
        LOG.exception("uc480テスト失敗: %s", stage)

    finally:
        # 6. 成功・失敗・Ctrl+Cのいずれでも、生成した機器の切断を試みる。
        if device is not None:
            try:
                if not device.disconnect() or device.is_connected():
                    raise RuntimeError("uc480の切断に失敗しました")
                LOG.info("切断完了: uc480")
            except Exception as exc:
                result["passed"] = False
                result["disconnect_error"] = str(exc)
                LOG.exception("uc480の切断失敗")
            finally:
                device.logger.removeHandler(LOG.handlers[-1])

        # 7. PNGの場所や取得件数を含む結果をJSONへ保存する。
        finish_output(result, output)

    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
