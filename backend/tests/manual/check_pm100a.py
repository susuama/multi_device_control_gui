r"""PM100Aの実機テスト。下の設定を編集し、引数なしで実行する。

リポジトリ直下から:
    .\.venv\Scripts\python.exe -m backend.tests.manual.check_pm100a
"""

from pathlib import Path

from backend.app.devices.power_meter.Thorlabs_PM100A import PM100A
from backend.tests.manual.hardware_check_common import (
    LOG,
    capture_cycle,
    finish_output,
    positive_seconds,
    prepare_output,
)


# ==================== ここを編集する ====================

# 最初はTrueで実行してVISAアドレスを調べる。
# 接続テストを行うときはFalseに変更する。
SCAN_ONLY = True

# 探索結果のvisa_resourcesに表示された、PM100Aのアドレスを貼り付ける。
VISA_ADDRESS = "USB0::0x1313::0x8079::P1005116::INSTR"

# 取得を2回行う。それぞれの取得時間を秒で指定する。
CAPTURE_SECONDS = 5.0

# このフォルダーの下へ、pm100a/日時/test.logとresult.jsonを保存する。
OUTPUT_ROOT = Path("backend/tests/test-results/hardware")


def main() -> int:
    """PM100Aの探索、または接続・設定・取得・切断を順に確認する。"""
    seconds = positive_seconds(CAPTURE_SECONDS)
    output = prepare_output("pm100a", OUTPUT_ROOT)

    result = {"device": "pm100a", "passed": False, "cycles": []}
    device = None
    stage = "探索"

    try:
        if SCAN_ONLY:
            # ドライバーの探索結果と、VISA自体のリソース一覧を別々に表示する。
            # ドライバーのフィルターで見つからなくても、接続先を調べられる。
            import pyvisa

            manager = pyvisa.ResourceManager()
            try:
                result["visa_resources"] = list(manager.list_resources())
            finally:
                manager.close()

            result["discovered"] = [item.model_dump(mode="json") for item in PM100A.scan()]

            LOG.info("VISAアドレス一覧: %s", result["visa_resources"])
            LOG.info("PM100Aドライバーの探索結果: %s", result["discovered"])

        else:
            if not VISA_ADDRESS:
                raise ValueError("ファイル先頭のVISA_ADDRESSを指定してください")

            # 1. 指定したPM100Aへ接続する。
            stage = "接続"
            device = PM100A(device_id="hardware-pm100a", address=VISA_ADDRESS)
            device.logger.addHandler(LOG.handlers[-1])

            if not device.connect() or not device.is_connected():
                raise RuntimeError("PM100Aの接続状態を確認できませんでした")

            # 2. 機器情報と、機器から読み戻した現在の設定を記録する。
            stage = "機器情報・設定取得"
            result["info"] = device.get_info().model_dump(mode="json")
            result["initial_settings"] = device.get_settings().model_dump(mode="json")

            LOG.info("接続完了: %s", result["info"])
            LOG.info("現在の設定: %s", result["initial_settings"])

            # 3. 現在の設定を再適用し、設定書き込みと読み戻しを確認する。
            # 波長やレンジを任意の新しい値に変更するテストではない。
            stage = "停止中の設定再適用"
            applied = device.set_settings(device.get_settings())
            result["applied_settings"] = applied.model_dump(mode="json")

            # 4. パワーを取得する。途中で設定を再適用し、取得が続くことも確認する。
            stage = "1回目の取得・停止"
            result["cycles"].append(capture_cycle(device, seconds, output, cycle=1))

            # 5. 停止した後、再度取得できることを確認する。
            stage = "2回目の取得・停止"
            result["cycles"].append(capture_cycle(device, seconds, output, cycle=2))

        result["passed"] = True

    except Exception as exc:
        result["error"] = {"stage": stage, "type": type(exc).__name__, "message": str(exc)}
        LOG.exception("PM100Aテスト失敗: %s", stage)

    finally:
        # 6. 成功・失敗・Ctrl+Cのいずれでも、生成した機器の切断を試みる。
        if device is not None:
            try:
                if not device.disconnect() or device.is_connected():
                    raise RuntimeError("PM100Aの切断に失敗しました")
                LOG.info("切断完了: PM100A")
            except Exception as exc:
                result["passed"] = False
                result["disconnect_error"] = str(exc)
                LOG.exception("PM100Aの切断失敗")
            finally:
                device.logger.removeHandler(LOG.handlers[-1])

        # 7. 成功・失敗と、どの段階まで確認できたかをJSONへ保存する。
        finish_output(result, output)

    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
