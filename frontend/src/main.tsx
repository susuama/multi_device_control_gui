import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { logger } from './logging/logStore'
import { applicationRuntime } from './state/applicationRuntime'
import { backendStore } from './state/backendStore'
import { disposeWebSocketClients } from './api/websocketClient'

const onError = (event: ErrorEvent) => logger.error('frontend', event.message);
const onUnhandledRejection = (event: PromiseRejectionEvent) => {
  logger.error('frontend', event.reason instanceof Error ? event.reason.message : String(event.reason));
};
window.addEventListener('error', onError);
window.addEventListener('unhandledrejection', onUnhandledRejection);
logger.info('frontend', 'アプリケーションを起動しました。');
const unsubscribeInitialScan = applicationRuntime.subscribe(() => {
  if (applicationRuntime.getSnapshot().phase !== 'ready') return;
  unsubscribeInitialScan();
  void backendStore.scanDevices().catch((cause) =>
    logger.error('frontend', `デバイスの自動探索に失敗しました。${cause instanceof Error ? cause.message : String(cause)}`)
  );
});
void applicationRuntime.start().catch((cause) =>
  logger.error('core', cause instanceof Error ? cause.message : String(cause))
);
const disposeCore = () => {
  unsubscribeInitialScan();
  applicationRuntime.dispose();
  disposeWebSocketClients();
};
window.addEventListener('pagehide', disposeCore);

// 開発中にモジュールが再読み込みされた場合も、同じエラーを重複して記録しない。
import.meta.hot?.dispose(() => {
  window.removeEventListener('error', onError);
  window.removeEventListener('unhandledrejection', onUnhandledRejection);
  window.removeEventListener('pagehide', disposeCore);
  disposeCore();
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
