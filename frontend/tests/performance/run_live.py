"""実バックエンドのダミーで全件保持・表示・設定変更・CSV記録を確認する。"""
import argparse
import csv
from datetime import datetime, timedelta
import json
import logging
import os
from pathlib import Path
import socket
import sys
import threading
import time
import traceback

from run import process_times

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--comparison', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    import uvicorn
    import webview
    from fastapi.staticfiles import StaticFiles
    from backend.app.main import create_app
    from desktop.websocket_protocol import ShutdownSafeWebSocketProtocol
    logging.disable(logging.CRITICAL)
    app = create_app()
    app.mount('/', StaticFiles(directory=args.build, html=True))
    server_ms = []

    @app.middleware('http')
    async def timing(request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        if request.method == 'GET' and request.url.path.endswith('/settings'):
            server_ms.append((time.perf_counter() - started) * 1000)
        return response

    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    server = uvicorn.Server(uvicorn.Config(app, ws=ShutdownSafeWebSocketProtocol, log_config=None))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    while not server.started:
        time.sleep(.02)
    recorder = app.state.measurement_manager._data_recorder
    write = recorder.write
    recorded = 0

    def track_write(sample, elapsed_s=None):
        nonlocal recorded
        active = recorder.is_recording() and sample.type == 'sample'
        write(sample, elapsed_s)
        if active:
            recorded += 1
    recorder.write = track_write
    window = webview.create_window('継続取得の表示検証', f'http://127.0.0.1:{listener.getsockname()[1]}/tests/performance/fixture.html', width=1200, height=900)
    report = {'ok': False, 'seconds_per_view': args.seconds, 'runs': []}

    def evaluate(expression):
        window.evaluate_js(f'window.liveResult=null; Promise.resolve({expression}).then(value=>window.liveResult={{value}},error=>window.liveResult={{error:String(error)}}); void 0;')
        deadline = time.monotonic() + 30
        while True:
            result = window.evaluate_js('window.liveResult')
            if result is not None:
                if 'error' in result:
                    raise RuntimeError(result['error'])
                return result.get('value')
            if time.monotonic() > deadline:
                raise TimeoutError(expression)
            time.sleep(.05)

    def verify():
        started = time.monotonic()
        try:
            deadline = time.monotonic() + 30
            while not window.evaluate_js('Boolean(window.performanceTest)'):
                if time.monotonic() > deadline:
                    raise TimeoutError('検証画面の初期化')
                time.sleep(.1)
            initializer = 'initializeComparison' if args.comparison else 'initializeReal'
            report['initial'] = evaluate(f'window.performanceTest.{initializer}({json.dumps(str(output / "recordings"))})')
            print('WARMUP 1000 real samples', flush=True)
            deadline = time.monotonic() + 160
            count = 'comparisonCount' if args.comparison else 'realCount'
            while window.evaluate_js(f'window.performanceTest.{count}()') < 1000:
                if time.monotonic() > deadline:
                    raise TimeoutError('実取得1000件')
                time.sleep(2)
            for view in (['graph'] if args.comparison else ['table', 'graph', 'log']):
                evaluate('window.performanceTest.showComparison()' if args.comparison else f'window.performanceTest.showReal({json.dumps(view)})')
                print(f'START live {view}', flush=True)
                previous, at = process_times(), time.perf_counter()
                samples = []
                for _ in range(args.seconds):
                    time.sleep(1)
                    current, now = process_times(), time.perf_counter()
                    sample = {group: sum(cpu - previous[pid][1] for pid, (name, cpu) in current.items() if name == group and pid in previous) / (now - at) * 100 / os.cpu_count() for group in ['python', 'webview']}
                    samples.append(sample)
                    previous, at = current, now
                frontend = window.evaluate_js('window.performanceTest.stop()')
                if frontend['errors'] or frontend['settingsErrors']:
                    raise AssertionError(frontend)
                report['runs'].append({'view': view, 'frontend': frontend, 'average': {group: sum(sample[group] for sample in samples) / len(samples) for group in ['python', 'webview']}, 'samples': samples})
                print(json.dumps({'view': view, 'average': report['runs'][-1]['average']}), flush=True)
            final = evaluate('window.performanceTest.finishComparison()' if args.comparison else 'window.performanceTest.finishReal()')
            assert not final['errors'], final['errors']
            folder = Path(report['initial']['recording']['directory'])
            metadata = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
            measurement_start = datetime.fromisoformat(metadata['measurement_started_at'])
            tails = final['tails'] if args.comparison else {report['initial']['meterId']: final['tail']}
            rows_count = 0
            for device_id, tail in tails.items():
                with (folder / f'{device_id}.csv').open(encoding='utf-8-sig', newline='') as file:
                    rows = list(csv.DictReader(file))
                rows_count += len(rows)
                lookup = {float(row['elapsed_s']): row for row in rows}
                received = {event['elapsed_s']: event for event in tail}
                assert all(float(row['elapsed_s']) in received for row in rows), device_id
                for event in tail:
                    saved = lookup.get(event['elapsed_s'])
                    if saved is None:
                        # 記録開始前の受信も履歴に残る。記録区間内の欠落は許容しない。
                        assert event['elapsed_s'] < min(lookup), event
                    else:
                        assert float(saved['power_w']) == event['data']['power_w'], event
                        assert int(saved['timestamp_ns']) == event['timestamp_ns'], event
                    expected = measurement_start + timedelta(seconds=event['elapsed_s'])
                    assert abs((datetime.fromisoformat(event['datetime']) - expected).total_seconds()) <= 1e-6, event
            assert rows_count == recorded > 1000, (rows_count, recorded)
            assert metadata['recording_stopped_at'] is not None
            report.update(ok=True, elapsed_seconds=time.monotonic() - started, csv_rows=rows_count, accepted_samples=recorded,
                tail_matches_csv=rows_count, retained_samples=sum(len(tail) for tail in tails.values()),
                server_settings_ms=server_ms, final_recording=final['recording'])
        except Exception:
            report['error'] = traceback.format_exc()
            print(report['error'], flush=True)
        finally:
            (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f'DONE live ok={report["ok"]}', flush=True)
            window.destroy()
    try:
        webview.start(verify, gui='edgechromium', private_mode=False, storage_path=str(output / 'profile'))
    finally:
        server.should_exit = True
        thread.join(15)
        listener.close()
    manager = app.state.measurement_manager
    report['shutdown'] = {'server_stopped': not thread.is_alive(), 'processor_stopped': manager._processor_thread is None,
        'pending_cleanup': manager._data_recorder.has_pending_cleanup(),
        'devices_disconnected': all(not device.connected and not device.is_sampling() for device in app.state.device_manager.get_all().values())}
    report['ok'] &= all(report['shutdown'][key] for key in ['server_stopped', 'processor_stopped', 'devices_disconnected']) and not report['shutdown']['pending_cleanup']
    (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
