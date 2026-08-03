@echo off
setlocal
set "MATRIX_BASE_HOME="
for /f "tokens=2 delims==" %%A in ('findstr /b /c:"home = " "%~dp0.venv\pyvenv.cfg"') do set "MATRIX_BASE_HOME=%%A"
for /f "tokens=*" %%A in ("%MATRIX_BASE_HOME%") do set "MATRIX_BASE_HOME=%%A"
if not defined MATRIX_BASE_HOME exit /b 1
set "PYTHONPATH=%~dp0src;%~dp0.venv\Lib\site-packages"
"%MATRIX_BASE_HOME%\pythonw.exe" -m matrix_auto_cutter.product_startup
exit /b %ERRORLEVEL%
