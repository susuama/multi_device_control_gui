@echo off
setlocal
cd /d "%~dp0"
if not exist "node_modules\vite\bin\vite.js" (
  echo The existing frontend dependencies are required.
  pause
  exit /b 1
)
echo A2 reference demo: http://127.0.0.1:5175/design-demo.html
echo Keep this window open. Press Ctrl+C to stop the demo server.
node "node_modules\vite\bin\vite.js" --config vite.demo.config.ts
endlocal
