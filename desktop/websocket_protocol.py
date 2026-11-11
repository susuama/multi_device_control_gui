"""デスクトップ終了時のWebSocket切断競合を処理する。"""

from uvicorn.protocols.websockets.websockets_sansio_impl import WebSocketsSansIOProtocol
from websockets.protocol import CLOSED, CLOSING


class ShutdownSafeWebSocketProtocol(WebSocketsSansIOProtocol):
    """切断開始済みの接続にはCloseフレームを再送せず、transportを終了する。"""

    def shutdown(self) -> None:
        """実際の接続状態を終了判定へ反映し、Uvicornの後始末を実行する。"""
        # Uvicorn 0.54.0は相手側からの切断時にclose_sentがFalseのまま残り得る。
        # websockets側は既にClose応答を送っているため、再送せず既存の終了経路へ進む。
        if self.conn.state in (CLOSING, CLOSED):
            self.close_sent = True
        super().shutdown()
