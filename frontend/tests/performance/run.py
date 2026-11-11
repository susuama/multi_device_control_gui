"""同じ有限入力をWebView2で再生し、表示負荷を製品ビルドから分離して比較する。"""
import argparse
import ctypes
from ctypes import wintypes
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
sys.path.insert(0, str(REPO / 'frontend/tests/pywebview'))


class ProcessEntry(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD), ('pid', wintypes.DWORD),
        ('heap', ctypes.c_size_t), ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
        ('parent', wintypes.DWORD), ('priority', wintypes.LONG), ('flags', wintypes.DWORD),
        ('exe', wintypes.WCHAR * 260)]


kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel.OpenProcess.restype = wintypes.HANDLE
kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
kernel.CloseHandle.argtypes = [wintypes.HANDLE]


def process_times():
    """検証Pythonと、その子孫のWebView2だけの累積CPU秒を返す。"""
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    entries = {}
    entry = ProcessEntry()
    entry.size = ctypes.sizeof(entry)
    try:
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            entries[entry.pid] = (entry.parent, entry.exe)
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    descendants = {os.getpid()}
    while True:
        found = {pid for pid, (parent, _) in entries.items() if parent in descendants}
        if found <= descendants:
            break
        descendants |= found
    result = {}
    for pid in descendants:
        if pid != os.getpid() and 'msedgewebview2' not in entries.get(pid, ('', ''))[1].lower():
            continue
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            continue
        times = [wintypes.FILETIME() for _ in range(4)]
        try:
            if kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
                seconds = sum((value.dwHighDateTime << 32) + value.dwLowDateTime for value in times[2:]) / 1e7
                result[pid] = ('python' if pid == os.getpid() else 'webview', seconds)
        finally:
            kernel.CloseHandle(handle)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--skip-build', action='store_true')
    parser.add_argument('--conditions', nargs='+')
    parser.add_argument('--skip-profiles', action='store_true')
    parser.add_argument('--profiles-only', action='store_true')
    parser.add_argument('--build', type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    build = args.build.resolve() if args.build else output / 'build'
    if not args.skip_build:
        for command in ([shutil.which('node'), 'node_modules/typescript/bin/tsc', '-p', 'tests/performance/tsconfig.json'],
                        [shutil.which('node'), 'node_modules/vite/bin/vite.js', 'build', '--config', 'tests/performance/vite.config.ts']):
            subprocess.run(command, cwd=REPO / 'frontend', env={**os.environ, 'PERFORMANCE_BUILD_DIR': str(build)}, check=True)
    import uvicorn
    import webview
    from mock_api import create_mock_app
    logging.disable(logging.CRITICAL)
    app = create_mock_app(build)
    server_times = []

    @app.middleware('http')
    async def timing(request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        if request.url.path.endswith('/settings'):
            server_times.append((time.perf_counter() - started) * 1000)
        return response

    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    server = uvicorn.Server(uvicorn.Config(app, log_config=None))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    while not server.started:
        time.sleep(.02)
    window = webview.create_window('表示性能の検証', f'http://127.0.0.1:{listener.getsockname()[1]}/tests/performance/fixture.html', width=1200, height=900)
    report = {'ok': False, 'seconds': args.seconds, 'logical_cpus': os.cpu_count(), 'runs': []}

    def evaluate_async(expression):
        window.evaluate_js(f'window.probeResult=null; Promise.resolve({expression}).then(value=>window.probeResult={{value}},error=>window.probeResult={{error:String(error)}}); void 0;')
        deadline = time.monotonic() + 30
        while True:
            result = window.evaluate_js('window.probeResult')
            if result is not None:
                if 'error' in result:
                    raise RuntimeError(result['error'])
                return result.get('value')
            if time.monotonic() > deadline:
                raise TimeoutError(expression)
            time.sleep(.05)

    def verify():
        try:
            deadline = time.monotonic() + 30
            while not window.evaluate_js('Boolean(window.performanceTest)'):
                if time.monotonic() > deadline:
                    raise TimeoutError('検証画面の初期化')
                time.sleep(.1)
            evaluate_async('window.performanceTest.initialize()')
            report['environment'] = window.evaluate_js('({userAgent:navigator.userAgent,width:innerWidth,height:innerHeight,visibility:document.visibilityState})')
            conditions = [('log50', 'log', 50, 1000), ('log1000', 'log', 1000, 1000), ('logHidden', 'log', -1, 1000),
                ('table100', 'table', 100, 1000), ('table1000', 'table', 1000, 1000), ('tableHidden', 'table', -1, 1000),
                ('graphGrowing', 'graph', 1000, 100), ('graph1000', 'graph', 1000, 1000), ('graphPair1000', 'graphPair', 1000, 1000),
                ('graph2h', 'graph', 1000, 72000), ('graphPair2h', 'graphPair', 1000, 72000),
                ('graph2hRecent', 'graphRecent', 1000, 72000)]
            if args.profiles_only:
                conditions = []
            for label, view, limit, initial in conditions:
                if args.conditions and label not in args.conditions:
                    continue
                print(f'START {label}', flush=True)
                evaluate_async(f'window.performanceTest.start({json.dumps(view)},{limit},false,{initial})')
                time.sleep(3)
                previous = process_times()
                previous_at = time.perf_counter()
                samples = []
                for _ in range(args.seconds):
                    time.sleep(1)
                    current, now = process_times(), time.perf_counter()
                    sample = {'seconds': now - previous_at, 'webview': 0., 'python': 0.}
                    for pid, (group, cpu) in current.items():
                        if pid in previous:
                            sample[group] += (cpu - previous[pid][1]) / (now - previous_at) * 100 / os.cpu_count()
                    samples.append(sample)
                    previous, previous_at = current, now
                frontend = window.evaluate_js('window.performanceTest.stop()')
                if label.startswith('graph') and frontend['oldestElapsed'] != 0:
                    raise AssertionError('最初の点を保持していません。')
                if label.endswith('2h') and (frontend['graphRange'] != '7200' or any(count < 72000 for count in frontend['graphPoints'])
                        or len(frontend['graphPoints']) != (2 if view == 'graphPair' else 1)):
                    raise AssertionError(frontend)
                if label.endswith('2h') and (len(frontend['renderedPoints']) != len(frontend['graphPoints'])
                        or any(not 0 < reduced < original for reduced, original in zip(frontend['renderedPoints'], frontend['graphPoints']))):
                    raise AssertionError('min-maxによる点数削減を確認できません。')
                if label == 'graph2hRecent' and (frontend['retained'] < 72000 or frontend['graphRange'] != '30'
                        or not 0 < frontend['graphPoints'][0] < 1000):
                    raise AssertionError(frontend)
                if label == 'graphPair1000' and (len(frontend['graphPoints']) != 2
                        or any(not 1000 <= count <= frontend['retained'] for count in frontend['graphPoints'])):
                    raise AssertionError(frontend['graphPoints'])
                detail = {'condition': label, 'samples': samples, 'frontend': frontend,
                    'average': {group: sum(item[group] * item['seconds'] for item in samples) / sum(item['seconds'] for item in samples) for group in ['webview', 'python']},
                    'peak': {group: max(item[group] for item in samples) for group in ['webview', 'python']}}
                report['runs'].append(detail)
                (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps({key: detail[key] for key in ['condition', 'average', 'peak']}), flush=True)
            report['profiles'] = []
            if not args.skip_profiles:
                # 削除されたtrには親がないため、計測用のselectorをタグ判定へ補正する。
                # 古いビルドにも同じ補正を適用し、同じ日時整形呼出しを計測する。
                window.evaluate_js('''(() => {
                    const matches = Element.prototype.matches;
                    Element.prototype.matches = function(selector) { return selector === 'tbody tr' ? this.tagName === 'TR' : matches.call(this, selector); };
                    const format = Object.getOwnPropertyDescriptor(Intl.DateTimeFormat.prototype, 'format');
                    Object.defineProperty(Intl.DateTimeFormat.prototype, 'format', { configurable: true, get() {
                        const bound = format.get.call(this);
                        return (...args) => { const started=performance.now(); window.intlCalls++; const result=bound(...args); window.intlMs+=performance.now()-started; return result; };
                    }});
                })();''')
            profile_views = ['graph'] if args.conditions and 'graph2h' in args.conditions else ['graphPair'] if args.conditions and 'graphPair1000' in args.conditions else ['log', 'table', 'graph']
            for view in ([] if args.skip_profiles else profile_views):
                initial = 72000 if args.conditions and 'graph2h' in args.conditions else 1000
                evaluate_async(f'window.performanceTest.start({json.dumps(view)},1000,true,{initial})')
                window.evaluate_js('window.intlCalls=0; window.intlMs=0; performance.clearResourceTimings(); void 0;')
                time.sleep(5)
                report['profiles'].append({'view': view, **window.evaluate_js('window.performanceTest.stop()'),
                    **window.evaluate_js('({intlFormatCalls:window.intlCalls,intlFormatMs:window.intlMs,resources:performance.getEntriesByType("resource").filter(entry=>entry.name.endsWith("/settings")).map(entry=>({start:entry.startTime,responseStart:entry.responseStart,responseEnd:entry.responseEnd,duration:entry.duration}))})')})
            report['server_settings_ms'] = server_times
            report['ok'] = all(not run['frontend']['errors'] and not run['frontend']['settingsErrors'] for run in report['runs']) and all(
                not profile['errors'] and not profile['settingsErrors'] and (profile['view'] != 'graphPair' or len(profile['graphPoints']) == 2
                    and all(1000 <= count <= profile['retained'] for count in profile['graphPoints']))
                for profile in report['profiles'])
        except Exception:
            report['error'] = traceback.format_exc()
            print(report['error'], flush=True)
        finally:
            (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f'DONE ok={report["ok"]}', flush=True)
            window.destroy()
    try:
        webview.start(verify, gui='edgechromium', private_mode=False, storage_path=str(output / 'profile'))
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
