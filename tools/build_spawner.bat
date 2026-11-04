@echo off
rem Build the CnCNet spawner (CnCNet-Spawner.dll) and Syringe.exe from tools\third_party with VS 2022.
setlocal
call "C:\Program Files\Microsoft Visual Studio\2022\Professional\Common7\Tools\VsDevCmd.bat" -arch=x86 >nul || exit /b 1
msbuild "%~dp0third_party\yrpp-spawner\Spawner.sln" /m /v:minimal /p:Configuration=Release /p:Platform=x86 || exit /b 1
msbuild "%~dp0third_party\Syringe\Syringe.sln" /m /v:minimal /p:Configuration=Release /p:Platform=Win32 /p:PlatformToolset=v143 || exit /b 1
