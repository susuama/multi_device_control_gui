"""手動実機テストのQueue取得・画像保存・ログ保存に使う補助処理。"""

import json
import logging
import math
import time
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue


LOG = logging.getLogger("hardware-check")


def positive_seconds(value: float) -> float:
    """取得時間が正の有限値か検証する。"""
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("秒数は正の有限値を指定してください")
    return seconds


def save_frame(frame, settings, path: Path) -> None:
    """カメラの全取得画像をPNGへ保存する。保存ROIでの切り出しはしない。"""
    from PIL import Image

    image = memoryview(frame.image)
    shape = (settings.height, settings.width)
    mode = "L"
    raw_mode = "L"
    if settings.pixel_format != "mono8":
        shape += (3,)
        mode = "RGB"
        raw_mode = "BGR" if settings.pixel_format == "bgr24" else "RGB"
    if image.format != "B" or image.shape != shape or not image.c_contiguous:
        raise ValueError(f"画像形式が不正です: format={image.format}, shape={image.shape}")
    Image.frombytes(mode, (settings.width, settings.height), image.tobytes(), "raw", raw_mode).save(path)


def capture_cycle(device, seconds: float, output: Path, cycle: int) -> dict:
    """Queue経由の取得、取得中の設定適用、取得停止を確認する。

    Args:
        device: 接続済みの機器クラスのインスタンス。
        seconds (float): この回の取得時間。単位は秒。
        output (Path): カメラの確認画像を保存するフォルダー。
        cycle (int): 取得回数。1回目だけ取得中の設定再適用も確認する。

    Returns:
        dict: 取得件数、設定通知件数、最後の測定値や画像の情報。
    """
    queue = Queue(maxsize=256)
    count = 0
    settings_count = 0
    reapplied = False
    summary = {"cycle": cycle}
    started = time.monotonic()

    # 実アプリと同じ公開メソッドを使い、SDKのprivateメソッドは直接呼ばない。
    thread = device.start_sampling(queue)
    try:
        while time.monotonic() - started < seconds:
            if device.last_error:
                raise RuntimeError(device.last_error)
            if not thread.is_alive():
                raise RuntimeError("取得スレッドが途中で終了しました")
            try:
                data = queue.get(timeout=0.2)
            except Empty:
                continue

            if data.id != device.device_id:
                raise ValueError(f"異なるデバイスのデータです: {data.id}")

            # 設定通知は数え、測定値・画像はそれぞれの形式で確認する。
            if data.type == "settings":
                settings_count += 1

            elif data.type == "sample":
                power = float(data.data["power_w"])
                if not math.isfinite(power):
                    raise ValueError(f"パワーが有限値ではありません: {power}")
                summary["last_power_w"] = power
                summary["min_power_w"] = min(summary.get("min_power_w", power), power)
                summary["max_power_w"] = max(summary.get("max_power_w", power), power)
                count += 1

            elif data.type == "frame":
                frame = data.data
                image = memoryview(frame.image)
                if image.format != "B" or not image.c_contiguous:
                    raise ValueError("画像がC連続のuint8ではありません")
                summary["last_frame_index"] = frame.frame_index
                summary["last_device_timestamp_ns"] = frame.device_timestamp_ns
                if count == 0:
                    path = output / f"uc480-cycle-{cycle}.png"
                    save_frame(frame, device.get_settings(), path)
                    summary["image"] = str(path)
                count += 1

            else:
                raise ValueError(f"想定外のデータ種別です: {data.type}")

            if reapplied and settings_count >= 2 and data.type in ("sample", "frame"):
                summary["count_after_reapply"] = summary.get("count_after_reapply", 0) + 1

            # 2回目の取得では停止・再開のみを確認する。
            # 1回目では、画像／測定値が届いた後に設定を再適用して取得が続くか確認する。
            halfway = time.monotonic() - started >= seconds / 2

            if cycle == 1 and count and not reapplied and halfway:
                applied = device.set_settings(device.get_settings())
                summary["settings_during_capture"] = applied.model_dump(mode="json")
                reapplied = True
                summary["count_at_reapply"] = count
                LOG.info("取得中の設定再適用完了: %s", device.device_id)
    finally:
        # Ctrl+Cや検証失敗でも、まず取得スレッドを止める。
        device.stop_sampling()

    if device.last_error:
        raise RuntimeError(device.last_error)
    if thread.is_alive() or device.is_sampling():
        raise RuntimeError("停止後も取得スレッドが残っています")
    if count == 0 or settings_count == 0:
        raise RuntimeError(f"データが不足しています: count={count}, settings={settings_count}")
    if cycle == 1 and (not reapplied or summary.get("count_after_reapply", 0) == 0):
        raise RuntimeError("設定再適用後のデータを確認できませんでした。秒数を増やして再確認してください")
    summary.update(
        count=count,
        settings_notifications=settings_count,
        elapsed_seconds=time.monotonic() - started,
    )
    LOG.info("取得・停止完了: %s cycle=%s count=%s", device.device_id, cycle, count)
    return summary


def prepare_output(device_name: str, output_root: Path) -> Path:
    """日時別の保存先を作り、専用ロガーへ画面・ファイル出力を設定する。"""
    output = output_root / device_name / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True, exist_ok=False)

    # ドライバーがrootロガーを再設定しても、このログ出力は維持する。
    LOG.setLevel(logging.INFO)
    LOG.propagate = False
    LOG.handlers = [
        logging.StreamHandler(),
        logging.FileHandler(output / "test.log", encoding="utf-8"),
    ]

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for handler in LOG.handlers:
        handler.setFormatter(formatter)

    return output


def finish_output(result: dict, output: Path) -> None:
    """テスト結果をJSONへ保存し、ログファイルを閉じる。"""
    try:
        text = json.dumps(result, ensure_ascii=False, indent=2)
        (output / "result.json").write_text(text, encoding="utf-8")
        LOG.info("結果保存先: %s", output.resolve())
    finally:
        for handler in LOG.handlers:
            handler.close()
        LOG.handlers.clear()
