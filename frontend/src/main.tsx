import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { logger } from './logging/logStore'

const onError = (event: ErrorEvent) => logger.error('frontend', event.message);
const onUnhandledRejection = (event: PromiseRejectionEvent) => {
  logger.error('frontend', event.reason instanceof Error ? event.reason.message : String(event.reason));
};
window.addEventListener('error', onError);
window.addEventListener('unhandledrejection', onUnhandledRejection);
logger.info('frontend', 'アプリケーションを起動しました。');

// 開発中にモジュールが再読み込みされた場合も、同じエラーを重複して記録しない。
import.meta.hot?.dispose(() => {
  window.removeEventListener('error', onError);
  window.removeEventListener('unhandledrejection', onUnhandledRejection);
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
