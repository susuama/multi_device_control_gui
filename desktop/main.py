import webview
import uuid

class WindowManager:
    def __init__(self, base_url: str) -> None:
        """ウィンドウ管理クラスを初期化する。

        Args:
            base_url (str): Reactを表示するベースURL。
        """
        self.base_url = base_url
        # ウィンドウのリスト。キーはウィンドウID、値はpywebviewのWindowオブジェクト。
        self.windows: dict[str, webview.Window] = {}  

    def create_main_window(self) -> None:
        """メインウィンドウを作成する。"""
        window = webview.create_window(
            title="Research App",
            url=self.base_url,
            width=1200,
            height=800,
            text_select=True,
            zoomable=False,
        )
        window_id = str(uuid.uuid4())
        window.id = window_id  # ウィンドウに一意のIDを設定
        self.windows[window_id] = window

    def start(self) -> None:
        """pywebviewのGUIループを開始する。"""
        print("start")
        webview.start()

    # .......................................................
    # api からウィンドウを操作するためのメソッド

    def get(self, window_id: str) -> webview.Window | None:
        """指定されたIDのウィンドウを取得する。

        Args:
            window_id (str): ウィンドウのID。

        Returns:
            return (webview.Window | None): 指定されたIDのウィンドウ。存在しない場合はNone。
        """
        return self.windows.get(window_id, None)

    def get_all(self) -> list[webview.Window]:
        """現在表示されているウィンドウの一覧を取得する。

        Returns:
            return (list[webview.Window]): 現在表示されているウィンドウの一覧。
        """
        return list(self.windows.values())

    def new_window(self, title: str, path: str,) -> webview.Window:
        """新しいウィンドウを作成する。

        Args:
            title (str): ウィンドウタイトル。
            path (str): React側の表示パス。

        Returns:
            return: 作成されたpywebviewのWindow。
        """
        url = f"{self.base_url}{path}"

        window = webview.create_window(
            title=title,
            url=url,
            width=1000,
            height=700,
            text_select=True,
            zoomable=False,
        )

        window_id = str(uuid.uuid4())
        window.id = window_id
        self.windows[window_id] = window

        return window

    def all_destroy(self) -> None:
        """すべてのウィンドウを閉じる。"""
        for window in webview.windows[::-1]:
            window.destroy()