"""時刻取得に関する共通ユーティリティ。

時刻の扱い
----------
- get_counter_ns():
    測定データの時刻付け・経過時間・機器間同期に使用する。
    実際の日時を表す値ではなく、単調増加する高精度時計。
    単位は ns（ナノ秒）。
    データ取得の次の行で使用する。

- get_datetime():
    測定を実施した実際の日時を記録するために使用する。
    基本的には測定開始時に取得し、各測定点では get_counter_ns() を使用する。

推奨する使い分け
----------------
Device:
    測定値と get_counter_ns() の値を返す。

MeasurementManager:
    測定開始時に get_counter_ns() と get_datetime() を取得する。
    各Deviceの counter値との差から経過時間を計算する。
    測定開始時の実日時を基準に、各測定点の実日時を計算する。
"""

import time
from datetime import datetime, timedelta


NANOSECONDS_PER_SECOND = 1_000_000_000


def get_counter_ns() -> int:
    """単調増加する高精度な時刻をナノ秒単位で取得する。

    測定データのタイムスタンプ、経過時間の計算、
    複数機器間の時間同期に使用する。

    Returns:
        return (int): time.perf_counter_ns() による時刻 [ns]。
    """
    return time.perf_counter_ns()


def get_datetime() -> datetime:
    """現在のローカル日時をタイムゾーン情報付きで取得する。

    測定開始日時など、実際の日時を記録する用途に使用する。

    Returns:
        return (datetime): 現在のローカル日時。
    """
    return datetime.now().astimezone()


def counter_to_elapsed_seconds(
    counter_ns: int,
    start_counter_ns: int,
) -> float:
    """perf_counter_nsの差を経過時間[s]に変換する。

    Args:
        counter_ns (int): 対象データのカウンタ時刻 [ns]。
        start_counter_ns (int): 測定開始時のカウンタ時刻 [ns]。

    Returns:
        return (float): 測定開始からの経過時間 [s]。
    """
    return (
        counter_ns - start_counter_ns
    ) / NANOSECONDS_PER_SECOND


def counter_to_datetime(
    counter_ns: int,
    start_counter_ns: int,
    start_datetime: datetime,
) -> datetime:
    """カウンタ時刻を測定開始基準の実日時に変換する。

    Args:
        counter_ns (int): 対象データのカウンタ時刻 [ns]。
        start_counter_ns (int): 測定開始時のカウンタ時刻 [ns]。
        start_datetime (datetime): 測定開始時の実日時。

    Returns:
        return (datetime): 対象データに対応する実日時。
    """
    elapsed_seconds = counter_to_elapsed_seconds(
        counter_ns,
        start_counter_ns,
    )

    return start_datetime + timedelta(seconds=elapsed_seconds)