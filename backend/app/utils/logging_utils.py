import logging
from rich.logging import RichHandler


LEVEL = logging.DEBUG
_initialized = False

# 他のライブラリの内部ロガーのログレベルを上げて表示されないようにする
libs =[
    "pyvisa", 
    "PIL.PngImagePlugin",
    "matplotlib.font_manager"
]
for lib in libs:
    logging.getLogger(lib).setLevel(logging.WARNING)


def setup_logger(name="app", level=LEVEL):
    """
    ロガーをセットアップする。
    
    Args:
        name (str): ロガー名
        level (int): ログレベル
        
    Returns:
        logging.Logger: 設定されたロガー
    """
    global _initialized
    
    # 基本設定は一度だけ実行
    if not _initialized:
        # 既存のハンドラーをクリア
        root_logger = logging.getLogger()
        if root_logger.hasHandlers():
            root_logger.handlers.clear()
            
        logging.basicConfig(
            level=level,
            format="%(name)s - %(message)s",
            datefmt="[%X]",
            handlers=[RichHandler(markup=True, rich_tracebacks=True)]
        )
        _initialized = True
    
    # 名前付きロガーを取得
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    return logger