import threading
import queue
import time

_stop_signal = object()


def temp1(queue: queue.Queue) -> None:
    """Queueにデータを追加するスレッドの処理"""
    for i in range(5):
        data = f"temp 1 {i}"

        queue.put(data)
        print(data)
        time.sleep(1)


def temp2(queue: queue.Queue) -> None:
    """Queueからデータを取得するスレッドの処理"""
    for i in range(5):
        data = f"temp 2 {i}"

        queue.put(data)
        print(data)
        time.sleep(1)

def temp3(queue: queue.Queue) -> None:
    """Queueからデータを取得するスレッドの処理"""
    while True:
        try:
            data = queue.get(timeout=1)
            if data is _stop_signal:
                break

            print(f"監視スレッド: {data}")
        except:
            print("error")

        


if __name__ == "__main__":
    q = queue.Queue()

    thread1 = threading.Thread(target=temp1, args=(q,))
    thread2 = threading.Thread(target=temp2, args=(q,))
    thread3 = threading.Thread(target=temp3, args=(q,))

    thread1.start()
    thread2.start()
    thread3.start()

    thread1.join()
    thread2.join()

    time.sleep(2)

    q.put(_stop_signal)
    thread3.join()

    print("All tasks completed.")