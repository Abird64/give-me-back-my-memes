@echo off
chcp 65001 >nul
rem ============================================================
rem  《把我的表情还回来》 新版前端 —— 双击启动
rem  用法：双击本文件即可（或把它拖到桌面建快捷方式）。
rem  说明：这是**源码形态**，直接跑仓库里的 webui 前端，改代码即时生效；
rem        dist\把我的表情还回来.exe 是同一套前端的打包版。
rem ============================================================
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   [错误] 找不到虚拟环境 .venv
  echo   请在 D:\code\mymeme 目录下先跑过：.venv\Scripts\python.exe -m pip install -e .
  echo.
  pause
  exit /b 1
)
echo.
echo   正在启动《把我的表情还回来》……
echo   （第一次启动可能要等几秒；关掉本窗口即退出程序）
echo.
".venv\Scripts\python.exe" -m emoji_migrator.webui
if errorlevel 1 (
  echo.
  echo   [退出码 %errorlevel%] 程序异常退出，上面的报错信息是线索。
  pause
)
