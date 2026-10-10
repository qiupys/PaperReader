@echo off
setlocal
title PaperReader - AI 论文阅读器

rem 自动定位项目根：本文件放在仓库根（含 backend/）或其上一级（含 PaperReader/）均可
set "ROOT=%~dp0"
if exist "%ROOT%backend" goto ROOT_OK
set "ROOT=%~dp0PaperReader"
:ROOT_OK
set "BACKEND=%ROOT%\backend"
set "FRONTEND=%ROOT%\frontend"
set "URL=http://127.0.0.1:8765"
set "VYPY=%BACKEND%\.venv\Scripts\python.exe"
set "SHIM=%~dp0scripts\zen_free_shim.py"
set "LOGS=%~dp0logs"

echo ============================================
echo   PaperReader 一键启动
echo ============================================
echo.

rem 定位 uv：优先用默认安装位置，找不到再查 PATH
set "UV=%USERPROFILE%\.local\bin\uv.exe"
if exist "%UV%" goto HAVE_UV
where uv >nul 2>nul
if errorlevel 1 goto NO_UV
set "UV=uv"
:HAVE_UV

rem 若服务已在运行，直接打开浏览器
netstat -ano | findstr ":8765" | findstr "LISTENING" >nul 2>nul
if errorlevel 1 goto NOT_RUNNING
echo [提示] PaperReader 已在运行，直接打开页面...
start "" "%URL%"
timeout /t 2 /nobreak >nul
exit /b 0

:NOT_RUNNING
rem 首次运行：安装后端依赖
if exist "%BACKEND%\.venv\Scripts\python.exe" goto BACKEND_OK
echo [初始化] 首次运行，安装后端依赖（uv sync）...
pushd "%BACKEND%"
"%UV%" sync
if errorlevel 1 (
    popd
    goto FAIL_BACKEND
)
popd
:BACKEND_OK

rem 首次运行：构建前端
if exist "%FRONTEND%\dist\index.html" goto FRONTEND_OK
echo [初始化] 首次运行，构建前端（npm install + build）...
pushd "%FRONTEND%"
call npm install
if errorlevel 1 (
    popd
    goto FAIL_NPM
)
call npm run build
if errorlevel 1 (
    popd
    goto FAIL_BUILD
)
popd
:FRONTEND_OK

rem 启动免费模型转换层（端口 8766，内部会自动拉起 opencode serve:4096）
netstat -ano | findstr ":8766" | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 goto SHIM_OK
if not exist "%SHIM%" goto SHIM_SKIP
echo [启动] 免费模型转换层（8766）+ opencode serve（4096）...
if not exist "%LOGS%" mkdir "%LOGS%"
start "PaperReader-Shim" /min cmd /c ""%VYPY%" "%SHIM%" >> "%LOGS%\zen_free_shim.log" 2>&1"
timeout /t 3 /nobreak >nul
:SHIM_OK
:SHIM_SKIP

rem 检查 LLM 配置
if exist "%ROOT%\paperreader.toml" goto CONFIG_OK
if defined PAPERREADER_API_KEY goto CONFIG_OK
echo [提醒] 未检测到 LLM 配置：导入/总结/对话功能将不可用
echo        请把 paperreader.toml.example 复制为 paperreader.toml 并填入 API Key
echo.
:CONFIG_OK

rem 后台等待端口就绪后自动打开浏览器（最多等 60 秒）
start "" /min powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false;for($i=0;$i -lt 120;$i++){try{$t=New-Object Net.Sockets.TcpClient;$t.Connect('127.0.0.1',8765);$t.Close();$ok=$true;break}catch{Start-Sleep -Milliseconds 500}};if($ok){Start-Process 'http://127.0.0.1:8765'}"

echo [启动] 正在启动服务，浏览器将在就绪后自动打开：%URL%
echo        关闭本窗口即停止服务（转换层窗口可一并关闭）
echo.
cd /d "%BACKEND%"
"%UV%" run paper serve

echo.
echo [提示] 服务已停止。
pause
exit /b 0

:NO_UV
echo [错误] 未找到 uv，请先安装：https://docs.astral.sh/uv/
pause
exit /b 1

:FAIL_BACKEND
echo [错误] 后端依赖安装失败，请检查网络后重试。
pause
exit /b 1

:FAIL_NPM
echo [错误] npm install 失败，请检查网络/代理后重试。
pause
exit /b 1

:FAIL_BUILD
echo [错误] 前端构建失败。
pause
exit /b 1
