/** 実バックエンドとダミーデバイスを共有ストア経由で操作する、検証画面専用の手順。 */
export async function verifyRealBackend() {
    const checks: string[] = [];
    const snapshots: unknown[] = [];
    const api = window.coreTest;
    const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
    const require = (condition: unknown, message: string) => { if (!condition) throw new Error(message); };
    async function waitFor(predicate: () => unknown, message: string, timeout = 15000) {
        const deadline = performance.now() + timeout;
        while (performance.now() < deadline) {
            if (predicate()) return;
            await sleep(40);
        }
        throw new Error(`${message}: ${JSON.stringify(api.snapshot())}`);
    }
    async function control(command: string, extra = {}) {
        const response = await fetch('/_test/control', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command, ...extra }) });
        if (!response.ok) throw new Error(`検証制御失敗: ${command} ${await response.text()}`);
        return response.json();
    }
    const metrics = async () => (await fetch('/_test/metrics')).json();
    const writes = async () => JSON.stringify(Object.entries((await metrics()).requests).filter(([key]) => !key.startsWith('GET ')).sort());
    const ready = () => waitFor(() => api.snapshot().backend.synchronization.status === 'ready', '状態同期');
    async function action(...args: Parameters<typeof api.action>) {
        // 周期GETと競合していない同じJSターンで、操作可否の確認と送信を行う。
        await ready();
        return api.action(...args);
    }
    async function refresh() { await api.refresh(); await ready(); }
    function checkpoint(name: string) { checks.push(name); snapshots.push({ name, snapshot: api.snapshot() }); }
    let ok = false;
    let error: string | null = null;
    try {
        await ready();
        await api.scan();
        await refresh();
        const devices = api.snapshot().backend.devices ?? [];
        const camera = devices.find((device) => device.type === 'camera')?.id;
        const meters = devices.filter((device) => device.type === 'power_meter').map((device) => device.id);
        require(camera && meters.length === 2, 'ダミーカメラ1台・数値機器2台がありません。');
        if (!camera) throw new Error('カメラがありません。');
        const ids = [camera, ...meters];
        const directory = (await control('directory')).directory;
        api.setPreferences({ defaultDirectory: directory });
        await waitFor(() => document.querySelectorAll('[data-panel]').length === 2, '2パネル');
        const connect = document.querySelector<HTMLButtonElement>('[data-panel="1"] [data-operation="connect"]');
        require(connect && !connect.disabled, '画面から接続できません。');
        connect!.click();
        await waitFor(() => api.snapshot().backend.devices?.find((device) => device.id === camera)?.status === 'connected', '画面操作によるカメラ接続');
        await ready();
        for (const id of meters) await action('connect', id);
        const initial = api.snapshot().backend.settings[camera];
        await action('updateSettings', camera, { ...initial, width: 32, height: 24, fps: 20, exposure: 0.01, gain: 1,
            roi: { x: 0, y: 0, width: 16, height: 12 } });
        for (const id of ids) await action('startSampling', id);
        await waitFor(() => meters.every((id) => (api.snapshot().counts[id] ?? 0) >= 3) && !!api.snapshot().previews[camera], '数値・画像の受信');
        require(JSON.stringify(await api.decodePreview()) === JSON.stringify({ width: 32, height: 24 }), 'JPEGの実デコード寸法が異なります。');
        require((await metrics()).connections === 2, '数値1接続・画像1接続で共有されていません。');
        const panels = [...document.querySelectorAll('[data-panel] pre')];
        require(panels.length === 2 && panels[0].textContent === panels[1].textContent, '2パネルで確定状態が一致しません。');
        checkpoint('探索・画面からの接続・取得・2パネル共有・実JPEGデコード');

        await action('startRecording');
        const first = api.snapshot();
        const period = first.session!.measurement_id;
        const recordingId = first.backend.recording!.recording_id;
        const settings = first.backend.settings[camera];
        const beforeDenied = await writes();
        await ready();
        const denied = await api.action('updateSettings', camera, { ...settings, fps: 21 }).then(() => false, () => true);
        require(denied && beforeDenied === await writes(), 'FPSの拒否時にHTTP変更要求が送信されました。');
        await action('updateSettings', camera, { ...settings, gain: 2 });
        const atStart = api.snapshot().backend.recording!.targets[camera].settings_at_start;
        require('gain' in atStart && atStart.gain === 1, '開始時設定が変更されました。');
        await action('stopSampling', camera);
        require(api.snapshot().backend.recording!.targets[camera].state === 'recording', '個別取得停止で保存が完了扱いになりました。');
        await action('startSampling', camera);
        require(api.snapshot().session!.measurement_id === period, '個別再開で測定IDが変わりました。');
        checkpoint('記録開始時対象・設定ロック・ゲイン更新・個別取得停止と再開');

        await control('delay-stop');
        await ready();
        const stop = api.action('stopRecording');
        await waitFor(() => api.snapshot().backend.recording?.is_stopping, '終了処理中の状態取得');
        const reads = (await metrics()).requests['GET /api/recordings/status'];
        await sleep(1200);
        require((await metrics()).requests['GET /api/recordings/status'] > reads, '保存終了待ち中に状態GETが停止しました。');
        require(await api.action('stopRecording').then(() => false, () => true), '停止要求が重複送信されました。');
        await control('release-stop');
        await stop;
        require(api.snapshot().backend.recording!.recording_id === recordingId, '終了後に記録IDを失いました。');
        require(Object.values(api.snapshot().backend.recording!.targets).every((target) => target.state === 'completed'), '正常保存の完了状態がありません。');
        checkpoint('実CSV・動画保存・終了待ち中のGET・停止の重複防止');

        await action('startRecording');
        await control('queue-overflow');
        await waitFor(() => api.snapshot().backend.measurement?.sampling_device_ids.length === 0 && !!api.snapshot().backend.recording?.error, 'Queue超過の全取得停止');
        require(api.snapshot().backend.recording!.is_recording, 'Queue超過で記録が終了しました。');
        checkpoint('実Queueの301件超過・全取得停止・記録継続・エラー反映');
        await action('stopRecording');
        for (const id of ids) await action('startSampling', id);
        await waitFor(() => api.snapshot().session?.measurement_id !== period && meters.every((id) => !!api.snapshot().latest[id]), '新しい測定期間');
        const changed = api.snapshot();
        require(Object.values(changed.latest).every((event) => event?.measurement_id === changed.session?.measurement_id), '古い測定の数値が混在しています。');
        checkpoint('測定全体の再開始と履歴の切り替え');

        await action('startRecording');
        const oldTime = api.snapshot().latest[meters[0]]!.timestamp_ns;
        await control('fail-camera', { id: camera });
        await waitFor(() => api.snapshot().backend.recording?.targets[camera].state === 'failed', 'カメラ保存失敗');
        await waitFor(() => api.snapshot().latest[meters[0]]!.timestamp_ns > oldTime, '数値受信の継続');
        require(api.snapshot().backend.recording!.is_recording && api.snapshot().backend.recording!.targets[meters[0]].error === null, 'カメラ単独の失敗が記録全体を停止しました。');
        await ready();
        require(await api.action('stopRecording').then(() => false, () => true), '保存失敗の停止APIが成功扱いです。');
        await refresh();
        require(!api.snapshot().backend.recording!.is_stopping && !api.snapshot().backend.recording!.is_recording, '失敗後に終了処理が残っています。');
        checkpoint('カメラ保存の障害注入・数値保存継続・対象別結果と停止失敗');

        const writeCounts = await writes();
        const server = api.snapshot().session!.server_instance_id;
        const currentPeriod = api.snapshot().session!.measurement_id;
        await control('offline');
        await waitFor(() => api.snapshot().runtime.phase === 'error', '通信異常の表示');
        await sleep(250);
        const retained = JSON.stringify(api.snapshot().latest);
        await sleep(1100);
        require(retained === JSON.stringify(api.snapshot().latest), '通信断で確認済みの値が消去・変更されました。');
        await control('online');
        await ready();
        await waitFor(() => (api.snapshot().latest[meters[0]]?.timestamp_ns ?? 0) > JSON.parse(retained)[meters[0]].timestamp_ns, '自動再接続と受信復旧');
        require(writeCounts === await writes(), '再接続時に変更操作が自動送信されました。');
        require(api.snapshot().session!.measurement_id === currentPeriod, '同じサービスへの再接続で測定IDが変わりました。');
        checkpoint('HTTP/WS障害注入・最終値保持・再接続・変更要求の非再送');

        await control('restart');
        await waitFor(() => api.snapshot().session?.server_instance_id !== server && api.snapshot().runtime.phase === 'ready', '実サービス再起動の再同期');
        require(writeCounts === await writes(), 'サービス再起動時に変更操作が自動送信されました。');
        require(Object.keys(api.snapshot().counts).length === 0 && Object.keys(api.snapshot().previews).length === 0, '再起動前の履歴が残っています。');
        await api.scan();
        await refresh();
        for (const id of ids) await action('connect', id);
        await action('updateSettings', camera, { ...api.snapshot().backend.settings[camera], width: 32, height: 24, fps: 20, exposure: 0.01, gain: 1, roi: null });
        for (const id of ids) await action('startSampling', id);
        await waitFor(() => !!api.snapshot().latest[meters[0]] && !!api.snapshot().previews[camera], '新サービスからの受信');
        require(api.snapshot().latest[meters[0]]!.server_instance_id === api.snapshot().session!.server_instance_id, '再起動前の数値を採用しました。');
        checkpoint('実lifespan再起動・起動ID更新・古い履歴の破棄・明示再開');

        const beforePanel = await writes();
        document.querySelector<HTMLButtonElement>('[data-testid="toggle-panel"]')!.click();
        await waitFor(() => document.querySelectorAll('[data-panel]').length === 1, 'パネル解除');
        require((await metrics()).connections === 2 && beforePanel === await writes(), 'パネル解除で取得や共有接続が変わりました。');
        document.querySelector<HTMLButtonElement>('[data-testid="toggle-panel"]')!.click();
        await waitFor(() => document.querySelectorAll('[data-panel]').length === 2, 'パネル復帰');
        checkpoint('表示パネルの解除・再表示と取得の独立');
        require(api.snapshot().errors.length === 0, '画面で未処理の例外が発生しました。');
        api.dispose();
        const deadline = performance.now() + 5000;
        while ((await metrics()).connections !== 0 && performance.now() < deadline) await sleep(40);
        require((await metrics()).connections === 0, 'dispose後もWebSocketが残っています。');
        const stoppedRequests = JSON.stringify((await metrics()).requests);
        await sleep(1400);
        require(stoppedRequests === JSON.stringify((await metrics()).requests), 'dispose後も定期GET・再接続・機器操作が続いています。');
        checkpoint('runtime終了・WebSocketゼロ・定期GETと再接続待機の回収');
        ok = true;
    } catch (cause) {
        error = cause instanceof Error ? cause.stack ?? cause.message : String(cause);
        api.dispose();
        await control('release-stop').catch(() => {});
    }
    await fetch('/_test/result', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ok, checks, snapshots, error, userAgent: navigator.userAgent }) });
}
