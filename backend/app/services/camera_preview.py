"""カメラごとの最新画像をJPEGへ変換し、共有Broadcasterへ配信する。"""

import math
import threading
import time
from dataclasses import dataclass
from io import BytesIO

from PIL import Image

from backend.app.models.devices import CameraSetting
from backend.app.models.events import CameraPreviewEvent
from backend.app.models.measurement import CameraFrame
from backend.app.services.event_broadcaster import EventBroadcaster
from backend.app.utils.logging_utils import setup_logger
from backend.app.utils.time_utils import get_counter_ns


@dataclass(frozen=True)
class _PreviewFrame:
    """元バッファからコピーした画素と、その画像に対応する情報。"""

    pixels: bytes       # コピー済みの画素バッファ。
    width: int
    height: int
    pixel_format: str
    frame_index: int
    timestamp_ns: int


@dataclass
class _CameraState:
    """カメラの取得中に使うプレビュー状態。取得を開始するたびに新しく作成する。"""

    started_at_ns: int                       # 今回の取得開始時のPCカウンタ [ns]。これより前のフレームは受け付けない。
    pending: _PreviewFrame | None = None     # 変換待ちの最新1枚。Noneなら変換待ち画像がない。
    next_ready_at: float = 0.0               # 次のJPEG変換を開始できる時刻 [s]。time.monotonic()を基準とする。
    error: str | None = None                 # 直近のJPEG変換・配信予約エラー。正常処理後はNoneに戻す。


class CameraPreviewService:
    """最新画像を1つの専用スレッドで変換し、カメラIDをキーに配信する。

    変換待ちはカメラごとに1枚だけ保持し、変換中の画像は別に保持する。
    JPEG生成は購読者の有無によらず、取得中に行う。保存ROIは適用しない。
    """

    STOP_TIMEOUT = 2.0

    def __init__(
        self, event_broadcaster: EventBroadcaster, *, max_fps: float = 20.0, jpeg_quality: int = 80,
    ) -> None:
        """配信先と変換条件を保持し、変換待機スレッドを起動する。

        Args:
            event_broadcaster (EventBroadcaster): 数値配信と共有する配信先。
            max_fps (float): カメラごとの配信FPSの上限。有限の正数を指定する。
            jpeg_quality (int): JPEG品質。1から95までの整数を指定する。
        """
        if not math.isfinite(max_fps) or max_fps <= 0:
            raise ValueError("Preview max_fps must be finite and greater than zero.")
        if type(jpeg_quality) is not int or not 1 <= jpeg_quality <= 95:
            raise ValueError("JPEG quality must be an integer between 1 and 95.")
        
        self.event_broadcaster = event_broadcaster

        self._interval = 1.0 / max_fps                  # 同じカメラの処理完了から次の変換開始までの待機時間 [s]。
        self._jpeg_quality = jpeg_quality               # JPEG保存時にPillowへ渡す品質値（1～95）。
        self._condition = threading.Condition()         # カメラ状態の読み書きを同期し、変換スレッドの待機・通知に使う。
        self._cameras: dict[str, _CameraState] = {}     # カメラIDをキーに、今回の取得に対応する状態を保持する。
        self._closed = False                            # サービス終了後の画像受付と変換結果の配信を止めるフラグ。

        # 全カメラの変換と配信予約を行うスレッド。
        self._thread = threading.Thread(target=self._run, name="camera-preview")
        self._thread.start()

        self.logger = setup_logger(type(self).__name__)

    def start_camera(self, device_id: str) -> None:
        """取得開始時刻を記録し、以前の変換待ち画像と変換結果を無効にする。

        Args:
            device_id (str): 取得を開始するカメラのID。
        """
        with self._condition:
            if self._closed:
                raise RuntimeError("CameraPreviewService has been shut down.")
            self._cameras[device_id] = _CameraState(started_at_ns=get_counter_ns())
            self._condition.notify()

    def stop_camera(self, device_id: str) -> None:
        """指定カメラの変換待ち画像を破棄し、変換中の結果も配信対象から外す。"""
        with self._condition:
            self._cameras.pop(device_id, None)
            self._condition.notify()

    def offer_frame(
        self, device_id: str, frame: CameraFrame, timestamp_ns: int, settings: CameraSetting,
    ) -> bool:
        """画素をコピーし、指定カメラの変換待ち画像を最新の1枚へ置き換える。

        Args:
            device_id (str): 画像の取得元と配信キーに使うカメラID。
            frame (CameraFrame): 元画像とカメラ由来のフレーム番号。
            timestamp_ns (int): PC取得時のカウンタ時刻。
            settings (CameraSetting): この画像に対応する取得サイズと画素形式。

        Returns:
            bool: 画像を受け付けた場合はTrue。停止中・終了後・前回取得分ならFalse。
        """
        with self._condition:
            state = self._cameras.get(device_id)
            if state is None or self._closed or timestamp_ns < state.started_at_ns:
                return False

        # 画像をコピーし、画像の型・形状・メモリ配置を検証する
        image = memoryview(frame.image)
        shape = (settings.height, settings.width)
        if settings.pixel_format != "mono8":
            shape += (3,)
        if image.format != "B" or image.shape != shape:
            raise ValueError(f"Expected a {settings.pixel_format} uint8 image with shape {shape}.")
        if not image.c_contiguous:
            raise ValueError("Camera preview image must be C-contiguous.")

        # コピー済みの画素と、フレーム情報をまとめる
        snapshot = _PreviewFrame(
            pixels=image.tobytes(),
            width=settings.width, 
            height=settings.height,
            pixel_format=settings.pixel_format, 
            frame_index=frame.frame_index, 
            timestamp_ns=timestamp_ns,
        )

        with self._condition:
            # コピー中に停止・再開始されていないか、再確認する。新しい取得分を更新しない。
            if self._closed or self._cameras.get(device_id) is not state:
                return False
            
            state.pending = snapshot    # 変換待ちの最新1枚を更新する。古い画像は破棄される。
            self._condition.notify()
        return True

    def get_error(self, device_id: str) -> str | None:
        """直近の変換・配信エラーを返す。正常配信または再開始で解除する。"""
        with self._condition:
            state = self._cameras.get(device_id)
            return state.error if state is not None else None

    def shutdown(self) -> None:
        """画像の受付を終了し、変換スレッドの終了を待つ。

        待機上限を超えた場合はTimeoutErrorを送出し、再呼び出しで終了を確認できる。
        """
        with self._condition:
            self._closed = True
            self._cameras.clear()
            self._condition.notify()
        self._thread.join(timeout=self.STOP_TIMEOUT)
        if self._thread.is_alive():
            raise TimeoutError("Timed out waiting for camera preview conversion to stop.")

    def _encode_jpeg(self, frame: _PreviewFrame) -> bytes:
        """コピー済み画素の色順を指定して読み込み、JPEGのバイト列を返す。"""
        mode, raw_mode = {
            "mono8": ("L", "L"), "rgb24": ("RGB", "RGB"), "bgr24": ("RGB", "BGR"),
        }[frame.pixel_format]
        with Image.frombytes(
            mode, (frame.width, frame.height), frame.pixels, "raw", raw_mode, 0, 1,
        ) as image, BytesIO() as output:
            image.save(output, format="JPEG", quality=self._jpeg_quality, subsampling=0)
            return output.getvalue()

    def _run(self) -> None:
        """次の変換時刻が最も早いカメラの最新画像を変換し、対応するキーへ配信する。"""
        while True:
            with self._condition:
                while not self._closed:
                    # 変換待ち画像があるカメラを取得する。
                    candidates = [
                        (key, state) 
                        for key, state in self._cameras.items() 
                        if state.pending is not None
                        ]
                    if not candidates:
                        self._condition.wait()  # self._condition.notify() を待つ
                        continue

                    # next_ready_at が最小のカメラを選ぶ。変換時刻になっていなければ待機して続ける。
                    device_id, state = min(candidates, key=lambda item: item[1].next_ready_at)
                    delay = state.next_ready_at - time.monotonic()
                    if delay > 0:
                        self._condition.wait(timeout=delay) # self._condition.notify() を待つ
                        continue

                    frame = state.pending
                    state.pending = None
                    break
                else:
                    return

            try:
                event = CameraPreviewEvent(
                    id=device_id, 
                    frame_index=frame.frame_index, 
                    timestamp_ns=frame.timestamp_ns,
                    width=frame.width, 
                    height=frame.height, 
                    jpeg=self._encode_jpeg(frame),  # JPEG変換
                )
                with self._condition:
                    # 停止・再開始されていないことを確認して配信する
                    if self._closed or self._cameras.get(device_id) is not state:
                        continue
                    self.event_broadcaster.publish(event.model_dump(), key=device_id)
                    state.error = None

            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                report = False
                with self._condition:
                    if self._cameras.get(device_id) is state:
                        report = state.error != error
                        state.error = error
                if report:
                    self.logger.exception("Could not publish camera preview for %s", device_id)
            finally:
                with self._condition:
                    if self._cameras.get(device_id) is state:
                        # 変換時間のばらつきで連続送信しないよう、完了時刻から次の待機時間を数える。
                        state.next_ready_at = time.monotonic() + self._interval
