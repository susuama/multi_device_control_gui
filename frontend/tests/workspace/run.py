"""共有UI部品とワークスペースをEdge・WebView2で検証する。"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
from urllib.request import urlopen

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "frontend/tests/pywebview"))


def webview_worker(url, profile):
    """サイズ変更通知を検証するため、表示したWebView2で実行する。"""
    import webview
    window = webview.create_window("フロントエンド検証", url, width=1200, height=900)

    def wait():
        deadline = time.monotonic() + 150
        try:
            while time.monotonic() < deadline:
                with urlopen(url.split("/tests/")[0] + "/_test/result-state", timeout=3) as response:
                    if json.load(response)["done"]:
                        return
                time.sleep(0.2)
        finally:
            window.destroy()
    webview.start(wait, gui="edgechromium", private_mode=False, storage_path=str(profile))


def main():
    """分離したビルド・ポート・プロファイルで検証し、結果JSONを保存する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker-url")
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--suite", choices=("workspace", "devices"), default="workspace")
    parser.add_argument("--surface", choices=("all", "browser", "webview"), default="all")
    args = parser.parse_args()
    if args.worker_url:
        webview_worker(args.worker_url, args.profile)
        return 0

    import uvicorn
    from fastapi import Request
    from mock_api import create_mock_app

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    build = output / "build"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    node = shutil.which("node")
    for command in ([node, "node_modules/typescript/bin/tsc", "-p", f"tests/{args.suite}/tsconfig.json"],
                    [node, "node_modules/vite/bin/vite.js", "build", "--config", f"tests/{args.suite}/vite.config.ts"]):
        result = subprocess.run(command, cwd=REPO / "frontend", capture_output=True, text=True,
            encoding="utf-8", errors="replace", creationflags=flags,
            env={**os.environ, "WORKSPACE_TEST_BUILD_DIR": str(build)})
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
    edge = next((path for path in (
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
    ) if path.is_file()), None)
    if edge is None:
        raise RuntimeError("Microsoft Edgeが見つかりません。")
    report = {"ok": False, "suite": args.suite, "runs": []}
    surfaces = ("browser", "webview") if args.surface == "all" else (args.surface,)
    for surface in surfaces:
        owner = None
        if args.suite == "devices":
            import logging
            from run_real import VerificationBackend
            logging.disable(logging.INFO)
            (output / surface).mkdir(exist_ok=True)
            owner = VerificationBackend(build, output / surface)
            app = owner.app()
        else:
            app = create_mock_app(build)
        # 後から追加する検証専用経路を、静的配信・製品APIへの転送より前に置く。
        mount = app.router.routes.pop()
        result = None

        if owner is None:
            @app.post("/_test/result")
            async def receive_result(request: Request):
                nonlocal result
                result = await request.json()
                return {"ok": True}
        else:
            from starlette.responses import Response
            app.state.corrupt_next_settings = False
            app.state.settings_delay = 0
            app.state.settings_writes = []
            app.state.settings_active = 0
            app.state.settings_max_active = 0

            @app.post("/_test/fail-final-json")
            async def fail_final_json():
                recorder = owner.backend.state.measurement_manager._data_recorder
                original = recorder.write_metadata
                def fail(metadata):
                    if threading.current_thread().name == "recording-finalizer":
                        recorder.write_metadata = original
                        raise OSError("試験用の最終JSON保存失敗")
                    return original(metadata)
                recorder.write_metadata = fail
                return {"ok": True}

            @app.post("/_test/fail-next-video-start")
            async def fail_next_video_start():
                from backend.app.services import data_racorder
                original = data_racorder.VideoRecorder
                class FailingVideoRecorder(original):
                    def __init__(self, *args, **kwargs):
                        data_racorder.VideoRecorder = original
                        raise OSError("試験用の動画初期化失敗")
                data_racorder.VideoRecorder = FailingVideoRecorder
                return {"ok": True}

            @app.get("/_test/recording-files")
            async def recording_files():
                return {"files": [str(path) for path in (owner.output / "recordings").rglob("*") if path.is_file()]}

            @app.post("/_test/delay-next-settings")
            async def delay_next_settings(request: Request):
                app.state.settings_delay = (await request.json())["milliseconds"] / 1000
                return {"ok": True}

            @app.get("/_test/settings-writes")
            async def settings_writes():
                return {"writes": app.state.settings_writes, "max_active": app.state.settings_max_active}

            @app.post("/_test/fail-next-settings")
            async def fail_next_settings(request: Request):
                body = await request.json()
                device = owner.backend.state.device_manager.get(body["id"])
                original = device._apply_settings

                def fail(_settings):
                    device._apply_settings = original
                    raise RuntimeError("試験用の設定適用失敗")
                device._apply_settings = fail
                return {"ok": True}

            @app.post("/_test/corrupt-next-settings")
            async def corrupt_next_settings():
                app.state.corrupt_next_settings = True
                return {"ok": True}

            @app.middleware("http")
            async def corrupt_response(request: Request, call_next):
                settings_write = request.method == "PUT" and request.url.path.endswith("/settings")
                delay = 0
                if settings_write:
                    app.state.settings_writes.append({"path": request.url.path, "settings": await request.json(), "at": time.monotonic()})
                    app.state.settings_active += 1
                    app.state.settings_max_active = max(app.state.settings_max_active, app.state.settings_active)
                    delay, app.state.settings_delay = app.state.settings_delay, 0
                response = await call_next(request)
                if settings_write:
                    if delay:
                        await asyncio.sleep(delay)
                    app.state.settings_active -= 1
                if app.state.corrupt_next_settings and settings_write:
                    app.state.corrupt_next_settings = False
                    return Response("{", status_code=200, media_type="application/json")
                return response

        def current_result():
            return owner.result if owner is not None else result

        @app.get("/_test/result-state")
        async def result_state():
            return {"done": current_result() is not None}

        app.router.routes.append(mount)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        url = f"http://127.0.0.1:{listener.getsockname()[1]}/tests/{args.suite}/fixture.html"
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        thread.start()
        process = None
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("検証サーバーが起動しません。")
                time.sleep(0.05)
            command = ([str(edge), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                        "--window-size=1200,900", "--user-data-dir=" + str(output / "browser-profile"), url]
                if surface == "browser" else [sys.executable, __file__, "--output", str(output),
                    "--worker-url", url, "--profile", str(output / "webview-profile")])
            with (output / f"{surface}.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(command, creationflags=flags, stdout=log, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + 150
                while current_result() is None:
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("検証画面から結果が返りませんでした。")
                    time.sleep(0.2)
            result = current_result()
            detail = {"surface": surface, **result}
            if owner is not None:
                detail["settings_writes"] = app.state.settings_writes
                detail["settings_max_active"] = app.state.settings_max_active
                detail["recording_history"] = [{"path": str(path), "entries": {
                    device: len(history) for device, history in json.loads(path.read_text(encoding="utf-8")).get("devices", {}).items()
                }} for path in (owner.output / "recordings").rglob("metadata.json")]
            report["runs"].append(detail)
            if not result["ok"]:
                break
        except Exception:
            report["error"] = traceback.format_exc()
            break
        finally:
            if process is not None:
                if surface == "webview" and current_result() is not None:
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                elif process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            server.should_exit = True
            thread.join(timeout=15)
            listener.close()
            if thread.is_alive():
                report["shutdown_error"] = "検証サーバーを終了できませんでした。"
    report["ok"] = len(report["runs"]) == len(surfaces) and all(run["ok"] for run in report["runs"]) and "error" not in report and "shutdown_error" not in report
    path = output / "result.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "report": str(path)}), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
