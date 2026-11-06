"""実バックエンドを分離したブラウザー・WebView2検証に接続する。

テスト用HTTP経路で遅延・障害を注入するが、通常のバックエンドには経路を追加しない。
保存先・ビルド・プロファイル・結果は指定した出力ディレクトリへまとめる。
"""

import argparse
import asyncio
from contextlib import asynccontextmanager
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


class VerificationBackend:
    """製品APIの外側で通信を計測し、取得・保存へ検証用の障害を注入する。"""

    def __init__(self, build, output):
        self.build, self.output = build, output
        self.requests = {}
        self.connections = 0
        self.offline = False
        self.result = None
        self.release_json = threading.Event()
        self.release_json.set()
        self.release_processor = threading.Event()
        self.release_processor.set()
        self.processor_entered = threading.Event()

    async def start(self):
        """新しい製品サービス群を起動し、Processorと最終JSON保存へ待機ゲートを付ける。"""
        from backend.app.main import create_app
        self.backend = create_app()
        self.lifetime = self.backend.router.lifespan_context(self.backend)
        await self.lifetime.__aenter__()
        recorder = self.backend.state.measurement_manager._data_recorder
        write_metadata = recorder.write_metadata

        def write(metadata):
            if threading.current_thread().name == 'recording-finalizer':
                if not self.release_json.wait(15):
                    raise TimeoutError('Verification did not release final JSON.')
            return write_metadata(metadata)
        recorder.write_metadata = write
        mm = self.backend.state.measurement_manager
        elapsed = mm._get_elapsed

        def get_elapsed(counter, period):
            if not self.release_processor.is_set():
                self.processor_entered.set()
                if not self.release_processor.wait(15):
                    raise TimeoutError('Verification did not release Processor.')
            return elapsed(counter, period)
        mm._get_elapsed = get_elapsed

    async def close(self):
        """検証用の待機を解除し、製品のlifespanで取得・保存・配信を終了する。"""
        self.release_json.set()
        self.release_processor.set()
        await self.lifetime.__aexit__(None, None, None)

    async def control(self, body):
        command = body['command']
        if command == 'directory':
            return {'directory': str(self.output / 'recordings')}
        if command == 'delay-stop':
            self.release_json.clear()
        elif command == 'release-stop':
            self.release_json.set()
        elif command == 'queue-overflow':
            from backend.app.models.measurement import DeviceData
            from backend.app.utils.time_utils import get_counter_ns
            mm = self.backend.state.measurement_manager
            device = next(d for d in mm.device_manager.get_all().values() if d.get_info().type != 'camera' and d.is_sampling())
            source = device.data_queue
            self.processor_entered.clear()
            self.release_processor.clear()

            def fill():
                try:
                    source.put(DeviceData(device.device_id, 'sample', get_counter_ns(), {'power_w': 1.0}))
                    if not self.processor_entered.wait(3):
                        raise TimeoutError('Processor did not enter test gate.')
                    for _ in range(301):
                        source.put(DeviceData(device.device_id, 'sample', get_counter_ns(), {'power_w': 1.0}), block=False)
                    deadline = time.monotonic() + 5
                    while not mm._queue_stop_attempted and time.monotonic() < deadline:
                        time.sleep(0.01)
                    if not mm._queue_stop_attempted:
                        raise TimeoutError('Queue monitor did not attempt acquisition stop.')
                finally:
                    self.release_processor.set()
            await asyncio.to_thread(fill)
        elif command == 'fail-camera':
            mm = self.backend.state.measurement_manager
            video = mm._data_recorder._video_recorders[body['id']]

            def fail(_pixels):
                raise OSError('Injected camera persistence failure for integration verification.')
            video._write_frame = fail
        elif command == 'offline':
            self.offline = True
            for subscriber in tuple(self.backend.state.event_broadcaster._subscribers):
                if subscriber.full():
                    subscriber.get_nowait()
                    subscriber.task_done()
                subscriber.put_nowait(None)
        elif command == 'online':
            self.offline = False
        elif command == 'restart':
            await self.close()
            await self.start()
        else:
            raise ValueError(command)
        return {'ok': True}

    def app(self):
        """製品API・静的画面・検証専用経路を一つの分離サーバーへまとめる。"""
        from fastapi import FastAPI, Request
        from starlette.responses import JSONResponse
        from starlette.staticfiles import StaticFiles

        @asynccontextmanager
        async def lifespan(_app):
            await self.start()
            try:
                yield
            finally:
                await self.close()

        outer = FastAPI(lifespan=lifespan)

        @outer.get('/_test/metrics')
        async def metrics():
            return {'requests': dict(self.requests), 'connections': self.connections}

        @outer.post('/_test/control')
        async def control(request: Request):
            return await self.control(await request.json())

        @outer.post('/_test/result')
        async def result(request: Request):
            self.result = await request.json()
            return {'ok': True}

        static = StaticFiles(directory=self.build, html=True)

        async def gateway(scope, receive, send):
            path = scope['path']
            if path.startswith('/api/') or path == '/ws' or path.startswith('/ws/'):
                if scope['type'] == 'http':
                    key = scope['method'] + ' ' + path
                    self.requests[key] = self.requests.get(key, 0) + 1
                if self.offline:
                    if scope['type'] == 'http':
                        await JSONResponse({'detail': 'Verification transport unavailable'}, status_code=503)(scope, receive, send)
                    else:
                        await send({'type': 'websocket.close', 'code': 1013})
                    return
                if scope['type'] == 'websocket':
                    self.connections += 1
                    try:
                        await self.backend(scope, receive, send)
                    finally:
                        self.connections -= 1
                else:
                    await self.backend(scope, receive, send)
            else:
                await static(scope, receive, send)
        outer.mount('/', gateway)
        return outer


def webview_worker(url, profile):
    """分離プロファイルのWebView2で検証し、結果が送信されたらウィンドウを閉じる。"""
    os.environ['PYWEBVIEW_GUI'] = 'edgechromium'
    import webview
    window = webview.create_window('実バックエンド結合検証', url, width=1200, height=900, hidden=True)

    def wait():
        from urllib.request import urlopen
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            try:
                result = json.load(urlopen(url.split('/tests/')[0] + '/_test/result-state', timeout=2))
                if result['done']:
                    break
            except Exception:
                pass
            time.sleep(0.2)
        window.destroy()
    threading.Thread(target=wait, daemon=True).start()
    webview.start(gui='edgechromium', private_mode=False, storage_path=str(profile))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--worker-url')
    parser.add_argument('--profile', type=Path)
    args = parser.parse_args()
    if args.worker_url:
        webview_worker(args.worker_url, args.profile)
        return 0
    logging.disable(logging.INFO)
    import uvicorn
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    build = output / 'build'
    node = shutil.which('node')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    for command in ([node, 'node_modules/typescript/bin/tsc', '-p', 'tests/pywebview/tsconfig.json'],
                    [node, 'node_modules/vite/bin/vite.js', 'build', '--config', 'tests/pywebview/vite.config.ts']):
        compiled = subprocess.run(command, cwd=REPO / 'frontend', env={**os.environ, 'CORE_TEST_BUILD_DIR': str(build)},
                                  capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=flags)
        if compiled.returncode:
            raise RuntimeError(compiled.stdout + compiled.stderr)
    report = {'ok': False, 'runs': [], 'faults': 'test-only Processor/final-JSON gates, camera write failure and HTTP/WS unavailability'}
    edge = next((path for path in (Path(os.environ.get('PROGRAMFILES(X86)', '')) / 'Microsoft/Edge/Application/msedge.exe',
                                  Path(os.environ.get('PROGRAMFILES', '')) / 'Microsoft/Edge/Application/msedge.exe') if path.is_file()), None)
    if edge is None:
        raise RuntimeError('Microsoft Edge was not found.')
    for surface in ('browser', 'webview'):
        backend = VerificationBackend(build, output / surface)
        backend.output.mkdir(parents=True, exist_ok=True)
        app = backend.app()

        @app.get('/_test/result-state')
        async def state():
            return {'done': backend.result is not None}
        # mountより前に制御経路を置く。
        route = app.router.routes.pop()
        app.router.routes.insert(-1, route)
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        url = f'http://127.0.0.1:{listener.getsockname()[1]}'
        server = uvicorn.Server(uvicorn.Config(app, log_level='warning'))
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        thread.start()
        process = None
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError('Verification server did not start.')
                time.sleep(0.05)
            page = url + '/tests/pywebview/fixture.html?real-verification=1'
            if surface == 'browser':
                command = [str(edge), '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
                           '--user-data-dir=' + str(output / 'browser-profile'), page]
            else:
                command = [sys.executable, __file__, '--output', str(output), '--worker-url', page,
                           '--profile', str(output / 'webview-profile')]
            with (output / f'{surface}.log').open('w', encoding='utf-8') as log:
                process = subprocess.Popen(command, creationflags=flags, stdout=log, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + 150
                while backend.result is None and time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    time.sleep(0.1)
                run = backend.result or {'ok': False, 'error': 'No result before browser exit or deadline.'}
                run['surface'] = surface
                if surface == 'webview':
                    run['pywebview'] = importlib.metadata.version('pywebview')
                report['runs'].append(run)
                if not run['ok']:
                    break
        except Exception:
            report['error'] = traceback.format_exc()
            break
        finally:
            if process is not None:
                if surface == 'webview' and backend.result is not None:
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                elif process.poll() is None:
                    process.terminate()
                process.wait(timeout=10)
            server.should_exit = True
            thread.join(timeout=15)
            listener.close()
            if thread.is_alive():
                report['error'] = 'Verification server did not finish.'
            (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    report['ok'] = len(report['runs']) == 2 and all(run['ok'] for run in report['runs']) and 'error' not in report
    (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ok': report['ok'], 'report': str(output / 'result.json')}), flush=True)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
