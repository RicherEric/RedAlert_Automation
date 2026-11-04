@echo off
rem Dump YR class layouts (field offsets) from YRpp headers with MSVC. Compile only; nothing runs.
rem Usage: dump_layout.bat <path-to-YRpp-clone> <ClassName> [more classes...]
rem Example: dump_layout.bat C:\src\YRpp HouseClass TechnoClass FactoryClass
setlocal
set YRPP=%~1
shift
call "C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars32.bat" >nul
cd /d "%~dp0"
if not exist out mkdir out
:loop
if "%~1"=="" goto :eof
cl /nologo /c /DNOMINMAX /D_HAS_STD_BYTE=0 /D_CRT_SECURE_NO_WARNINGS /utf-8 /std:c++latest /permissive- /EHsc /I"%YRPP%" /Fo:out\layout.obj layout.cpp /d1reportSingleClassLayout%~1 > out\%~1.txt 2>&1
echo %~1 -^> out\%~1.txt
shift
goto loop
