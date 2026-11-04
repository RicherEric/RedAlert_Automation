@echo off
rem Build ra2bridge.dll and inject.exe (32-bit) with Visual Studio 2022.
rem YRpp is cloned into tools\third_party\YRpp at a pinned commit if it is missing.
setlocal
set HERE=%~dp0
set YRPP=%HERE%..\third_party\YRpp
set YRPP_COMMIT=f14642c8652bacdbc6b84833cedd006d1148a800
if not exist "%YRPP%\YRPP.h" (
  git clone https://github.com/Phobos-developers/YRpp "%YRPP%" || exit /b 1
  git -C "%YRPP%" checkout %YRPP_COMMIT% || exit /b 1
)
call "C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars32.bat" >nul || exit /b 1
cd /d "%HERE%"
if not exist out mkdir out
cl /nologo /LD /O2 /MT /EHsc /std:c++latest /permissive- /utf-8 /DNOMINMAX /D_HAS_STD_BYTE=0 ^
   /D_CRT_SECURE_NO_WARNINGS /D_WINSOCK_DEPRECATED_NO_WARNINGS /I"%YRPP%" ^
   bridge.cpp "%YRPP%\StaticInits.cpp" /Fo:out\ /Fe:out\ra2bridge.dll /link /NOLOGO || exit /b 1
cl /nologo /O2 /MT inject.cpp /Fo:out\ /Fe:out\inject.exe /link /NOLOGO || exit /b 1
echo built out\ra2bridge.dll out\inject.exe
