"""独立した模擬APIとWebView2でフロントエンド中核を検証する。"""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from urllib.request import Request, urlopen


REPO = Path(__file__).resolve().parents[3]
FRONTEND = REPO / "frontend"
FIXTURE = "/tests/pywebview/fixture.html"


def http_json(url: str, body: dict | None = None) -> dict:
    """検証用の制御・計測APIを呼び出す。"""
    request = Request(url, data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def verify_window(window, args) -> dict:
    """実際のWebView2内で操作し、共有状態とDOMを照合する。"""
    import webview
    from importlib.metadata import version
    from System import Func, String

    checks: list[str] = []

    def require(condition, message):
        if not condition:
            raise AssertionError(message)

    def wait_for(predicate, message, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.1)
        raise AssertionError(f"待機期限を超えました: {message}")

    def evaluate(expression):
        completed = threading.Event()
        result = []

        def done(value):
            result.append(value)
            completed.set()

        window.evaluate_js("Promise.resolve().then(() => (" + expression + "))"
            ".then(value => ({ok:true,value:value ?? null}), error => ({ok:false,error:String(error)}))", done)
        require(completed.wait(25), f"JavaScriptの応答がありません: {expression}")
        require(result[0]["ok"], result[0].get("error"))
        return result[0]["value"]

    def snapshot():
        return window.evaluate_js("window.coreTest?.snapshot()")

    def ready():
        return wait_for(lambda: (snapshot() or {}).get("backend", {}).get("synchronization", {}).get("status") == "ready", "状態同期")

    def when_ready(expression):
        # 確認と送信を同じJSターンで行い、周期GETとの間に操作を挟まない。
        return "new Promise(resolve => {const check=()=>{if(window.coreTest.snapshot().backend.synchronization.status==='ready')resolve();else setTimeout(check,20);};check();}).then(()=> (" + expression + "))"

    def action(operation, name="camera", settings=None):
        return evaluate(when_ready(f"window.coreTest.action({json.dumps(operation)}, {json.dumps(name)}, {json.dumps(settings) if settings is not None else 'undefined'})"))

    def metrics():
        return http_json(args.url + "/_test/metrics")

    def control(**command):
        return http_json(args.url + "/_test/control", command)

    def writes():
        return {key: count for key, count in metrics()["requests"].items() if not key.startswith("GET ")}

    wait_for(lambda: window.evaluate_js("!!window.coreTest"), "検証画面の読込み")
    ready()
    require(webview.guilib.renderer == "edgechromium", "WebView2以外の描画エンジンです。")
    result = {"phase": args.phase, "pywebview": version("pywebview"), "renderer": webview.guilib.renderer,
        "webview2_version": str(window.native.Invoke(Func[String](lambda: window.native.webview.CoreWebView2.Environment.BrowserVersionString))),
        "user_agent": window.evaluate_js("navigator.userAgent"), "checks": checks}
    expected_preferences = {"theme": "light", "defaultDirectory": "C:/core-test-日本語"}
    if args.phase == "restore":
        preferences = snapshot()["preferences"]
        require(all(preferences[key] == value for key, value in expected_preferences.items()), "設定を再起動後に復元できません。")
        require(window.evaluate_js("document.querySelector('main').dataset.theme") == "light", "復元した配色がDOMへ反映されません。")
        require(window.evaluate_js("document.querySelector('header input').value") == expected_preferences["defaultDirectory"], "復元した保存先がDOMへ反映されません。")
        checks.append("別プロセスでの設定復元")
    else:
        require(len(snapshot()["panels"]) == 2, "2パネルがありません。")
        checks.append("WebView2での初期化と2パネルの描画")
        ready()
        statuses = evaluate(when_ready("Promise.allSettled([window.coreTest.action('connect','camera'), window.coreTest.action('connect','camera')]).then(values => values.map(item => item.status))"))
        require(sorted(statuses) == ["fulfilled", "rejected"], "重複操作を拒否していません。")
        require(metrics()["requests"].get("POST /api/devices/camera/connect") == 1, "接続要求を重複送信しました。")
        for name in ["meter-1", "meter-2"]:
            action("connect", name)
        ready()
        wait_for(lambda: window.evaluate_js("(() => {const b=document.querySelector('[data-panel=\"1\"] [data-operation=\"startSampling\"]');if(!b || b.disabled || window.coreTest.snapshot().backend.synchronization.status!=='ready')return false;b.click();return true;})()"), "操作可能な開始ボタン")
        wait_for(lambda: snapshot()["backend"]["sampling"].get("camera"), "画面操作での取得開始")
        for name in ["meter-1", "meter-2"]:
            action("startSampling", name)
        wait_for(lambda: len(snapshot()["previews"]) == 1 and len(snapshot()["counts"]) == 2, "数値と画像の受信")
        require(evaluate("window.coreTest.decodePreview()") == {"width": 640, "height": 480}, "JPEGをデコードできません。")
        wait_for(lambda: window.evaluate_js("(() => {const p=[...document.querySelectorAll('[data-panel] pre')];return p.length===2 && p[0].textContent===p[1].textContent;})()"), "2パネルの表示一致")
        require(metrics()["connections"] == 2, "カメラ1接続・共通数値1接続で共有していません。")
        checks.append("画面からの共通操作・重複防止・数値とJPEGの共有")

        action("startRecording")
        settings = snapshot()["backend"]["settings"]["camera"]
        changed = {**settings, "fps": settings["fps"] + 1}
        before = writes()
        denied = evaluate(when_ready(f"window.coreTest.action('updateSettings','camera',{json.dumps(changed)}).then(() => false, error => String(error).includes('fps'))"))
        require(denied and writes() == before, "ロック中のFPSを送信しました。")
        action("updateSettings", settings={**settings, "gain": 2})
        require(snapshot()["backend"]["recording"]["targets"]["camera"]["settings_at_start"]["gain"] == 1, "開始時設定が変わりました。")
        ready()
        window.evaluate_js("window.stopResult=null; " + when_ready("window.coreTest.action('stopRecording')") + ".then(()=>window.stopResult='ok', error=>window.stopResult=String(error)); void 0;")
        wait_for(lambda: snapshot()["backend"]["recording"]["is_stopping"], "終了処理中の状態GET")
        denied = evaluate("window.coreTest.action('stopRecording').then(() => false, () => true)")
        require(denied, "進行中の停止を重複送信しました。")
        wait_for(lambda: window.evaluate_js("window.stopResult") == "ok", "記録停止完了")
        require(len(metrics()["active"]) == 3, "記録停止が取得を停止しました。")
        checks.append("設定ロック・開始時設定の保持・停止中のGET・取得との独立")

        control(invalid_start_response=True)
        ready()
        unknown = evaluate(when_ready("window.coreTest.action('startRecording').then(() => null, () => window.coreTest.snapshot().backend.requests['recording/start'].outcomeUnknown)"))
        require(unknown is True, "成功HTTPの不正応答を結果不明にしていません。")
        before = writes()
        evaluate("window.coreTest.refresh()")
        require(snapshot()["backend"]["recording"]["is_recording"] and writes() == before, "再確認で記録を再送しました。")
        action("stopRecording")
        checks.append("不正な変更応答の結果不明と、再送しない状態再確認")

        old_period = snapshot()["session"]["measurement_id"]
        control(new_measurement=True)
        evaluate("window.coreTest.refresh()")
        require(snapshot()["session"]["measurement_id"] != old_period, "測定期間が切り替わりません。")
        control(emit_old=True)
        wait_for(lambda: len(snapshot()["latest"]) == 2, "新しい測定の数値受信")
        time.sleep(0.4)
        data = snapshot()
        require(all(item["measurement_id"] == data["session"]["measurement_id"] for item in data["latest"].values()), "古い数値が混在しています。")
        require(data["previews"]["camera"]["measurement_id"] == data["session"]["measurement_id"], "古い画像が混在しています。")
        checks.append("測定切替と古い期間の遅延配信拒否")

        for count in [1, 2]:
            window.evaluate_js("document.querySelector('[data-testid=\"toggle-panel\"]').click()")
            wait_for(lambda: len(snapshot()["panels"]) == count, "パネルの切替")
            require(metrics()["connections"] == 2 and len(metrics()["active"]) == 3, "表示切替で接続か取得を停止しました。")
        window.minimize()
        time.sleep(1.2)
        window.restore()
        evaluate("window.coreTest.refresh()")
        require(snapshot()["runtime"]["phase"] == "ready", "最小化から復帰できません。")
        checks.append("パネル解除・再表示と最小化・復帰")

        before = writes()
        control(offline=True)
        wait_for(lambda: snapshot()["runtime"]["phase"] == "error" and metrics()["connections"] == 0, "通信断")
        require(snapshot()["counts"], "通信断で履歴が消えました。")
        control(offline=False)
        wait_for(lambda: snapshot()["runtime"]["phase"] == "ready" and metrics()["connections"] == 2, "通信の自動復旧")
        require(writes() == before, "通信復旧で変更要求を再送しました。")
        checks.append("通信断での値保持・自動復旧・変更要求の再送禁止")

        evaluate(f"window.coreTest.setPreferences({json.dumps(expected_preferences)})")
        require(not snapshot()["preferences"]["error"], "設定保存に失敗しました。")
        checks.append("設定の保存")
        samples = []
        started = time.monotonic()
        previous = snapshot()
        window.hide()
        while time.monotonic() - started < args.soak_seconds:
            time.sleep(max(0, min(30, args.soak_seconds - (time.monotonic() - started))))
            data = snapshot()
            require(all(count >= previous["counts"].get(name, 0) for name, count in data["counts"].items()), "受信履歴が失われました。")
            require(len(data["previews"]) == 1 and metrics()["connections"] == 2, "画像か接続の保持数が異常です。")
            require(data["runtime"]["phase"] == "ready", "継続受信中に中核が利用不能です。")
            require(data["previews"]["camera"]["frame_index"] > previous["previews"]["camera"]["frame_index"]
                and data["notifications"] > previous["notifications"], "継続受信中に更新が停止しました。")
            require(all(data["streams"][name]["lastReceivedAt"] > previous["streams"][name]["lastReceivedAt"]
                for name in ["camera", "meter-1", "meter-2"]), "機器別の受信が停止しました。")
            samples.append({"seconds": round(time.monotonic() - started, 1), "counts": data["counts"],
                "frame": data["previews"]["camera"]["frame_index"], "heap_bytes": data["heapBytes"],
                "notifications": data["notifications"], "metrics": metrics()})
            print(f"SOAK {samples[-1]['seconds']}s counts={data['counts']} heap={data['heapBytes']}", flush=True)
            previous = data
        result["soak"] = samples
        if args.soak_seconds:
            checks.append(f"継続受信 {args.soak_seconds:g}秒")
        require(not snapshot()["errors"], f"未処理の画面エラー: {snapshot()['errors']}")

    evaluate("window.coreTest.dispose()")
    wait_for(lambda: metrics()["connections"] == 0, "受信接続の解放")
    before = metrics()["requests"]
    time.sleep(2.2)
    require(metrics()["requests"] == before, "終了後も定期取得か再接続が続いています。")
    require(snapshot()["runtime"]["phase"] == "disposed", "共通層が終了していません。")
    checks.append("購読・定期取得・再接続待機の終了")
    result["metrics"] = metrics()
    return result


def worker(args) -> int:
    """GUIの初期化をプロセスごとに行い、設定の永続化も検証する。"""
    os.environ["PYWEBVIEW_GUI"] = "edgechromium"
    sys.path.insert(0, str(REPO))
    from desktop.main import WindowManager

    manager = WindowManager(args.url + FIXTURE)
    manager.create_main_window()
    window = next(iter(manager.windows.values()))
    result = {"ok": False, "phase": args.phase}
    started = threading.Event()

    def drive():
        try:
            result.update(verify_window(window, args), ok=True)
        except Exception:
            result["error"] = traceback.format_exc()
        finally:
            args.result.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            window.destroy()

    def loaded():
        if not started.is_set():
            started.set()
            threading.Thread(target=drive, daemon=True).start()

    window.events.loaded += loaded
    manager.start(storage_path=args.profile)
    return 0 if result["ok"] else 1


def main() -> int:
    """画面をビルドし、模擬APIと別プロセスのGUI検証を順に起動する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="検証結果JSONの保存先")
    parser.add_argument("--soak-seconds", type=float, default=0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--url", help=argparse.SUPPRESS)
    parser.add_argument("--profile", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--result", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--phase", choices=["first", "restore"], help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return worker(args)
    if args.output is None or args.soak_seconds < 0:
        parser.error("--outputを指定し、--soak-secondsには0以上を指定してください。")

    import uvicorn
    from mock_api import create_mock_app

    node = shutil.which("node")
    if node is None:
        raise RuntimeError("Node.jsがありません。")
    report = {"ok": False, "runs": []}
    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="multi-device-core-", dir=args.output.parent, ignore_cleanup_errors=True) as temporary:
        workspace = Path(temporary)
        build = workspace / "build"
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        for command in [[node, "node_modules/typescript/bin/tsc", "-p", "tests/pywebview/tsconfig.json"],
            [node, "node_modules/vite/bin/vite.js", "build", "--config", "tests/pywebview/vite.config.ts"]]:
            compiled = subprocess.run(command, cwd=FRONTEND, env={**os.environ, "CORE_TEST_BUILD_DIR": str(build)},
                creationflags=flags, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            if compiled.returncode:
                raise RuntimeError(compiled.stdout)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(uvicorn.Config(create_mock_app(build), log_level="error"))
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if time.monotonic() > deadline or not thread.is_alive():
                    raise RuntimeError("検証用サーバーを起動できません。")
                time.sleep(0.1)
            for phase in ["first", "restore"]:
                result_file = workspace / f"{phase}.json"
                command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--phase", phase,
                    "--url", url, "--profile", str(workspace / "profile"), "--result", str(result_file),
                    "--soak-seconds", str(args.soak_seconds if phase == "first" else 0)]
                log_path = args.output.with_suffix(f".{phase}.log")
                with log_path.open("w", encoding="utf-8") as log:
                    completed = subprocess.run(command, creationflags=flags, timeout=args.soak_seconds + 180,
                        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, stdout=log, stderr=subprocess.STDOUT)
                run = json.loads(result_file.read_text(encoding="utf-8")) if result_file.exists() else {
                    "ok": False, "error": f"{phase}: GUI結果ファイルがありません。終了コード {completed.returncode}"}
                report["runs"].append(run)
                if completed.returncode != 0 or not run["ok"]:
                    break
            report["ok"] = len(report["runs"]) == 2 and all(run["ok"] for run in report["runs"])
        except Exception:
            report["error"] = traceback.format_exc()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            listener.close()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "report": str(args.output)}, ensure_ascii=False), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
