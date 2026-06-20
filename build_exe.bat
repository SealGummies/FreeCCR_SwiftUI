@echo off
python write_version.py

for /f "delims=" %%i in ('python -c "import pyopencl, os; print(os.path.join(os.path.dirname(pyopencl.__file__), 'cl'))"') do set PYOPENCL_CL_DIR=%%i
echo PyOpenCL cl directory: %PYOPENCL_CL_DIR%

REM onnxruntime ships native DLLs (capi/onnxruntime*.dll) + a .pyd extension
REM that Nuitka must bundle, or the AI dust-detection feature is unavailable in
REM the compiled build. --include-package pulls the modules + extension and
REM --include-package-data copies the native DLLs alongside it.
REM See spec/dust-removal.md.

REM Use clang (bundled with Nuitka's mingw64) instead of gcc: gcc 14.2.0 hits an
REM internal compiler error (segfault in the ce1/if-conversion RTL pass) on the
REM large generated C for ccr_processor / PIL. clang 19 compiles those cleanly
REM (it is the compiler the Makefile's build-windows target already uses).
REM --jobs=6 bounds parallel C compilation so the few HUGE generated files
REM (ccr_processor, __helpers) don't spike memory enough to crash a compiler
REM process (24 cores would otherwise launch 24 at once).
nuitka --mingw64 --clang --jobs=6 --standalone --assume-yes-for-downloads --include-package=numpy --include-package=utils --enable-plugin=pyside6 ^
--include-data-dir=src/icons=icons --include-data-dir=LICENSES=LICENSES --windows-icon-from-ico=src/icons/freeccr_logo.ico ^
--windows-console-mode=attach --include-package=pyopencl --include-data-dir="%PYOPENCL_CL_DIR%=pyopencl/cl" ^
--include-package=onnxruntime --include-package-data=onnxruntime ^
--nofollow-import-to=doctest --nofollow-import-to=IPython ^
--nofollow-import-to=PIL.PdfParser --nofollow-import-to=PIL.PdfImagePlugin ^
--output-filename=freeccr.exe src/main.py