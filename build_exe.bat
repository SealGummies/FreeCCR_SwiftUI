@echo off
python write_version.py

for /f "delims=" %%i in ('python -c "import pyopencl, os; print(os.path.join(os.path.dirname(pyopencl.__file__), 'cl'))"') do set PYOPENCL_CL_DIR=%%i
echo PyOpenCL cl directory: %PYOPENCL_CL_DIR%

nuitka --mingw64 --standalone --assume-yes-for-downloads --include-package=numpy --include-package=utils --enable-plugin=pyside6 ^
--include-data-dir=src/icons=icons --include-data-dir=LICENSES=LICENSES --windows-icon-from-ico=src/icons/freeccr_logo.ico ^
--windows-console-mode=attach --include-package=pyopencl --include-data-dir="%PYOPENCL_CL_DIR%=pyopencl/cl" ^
--nofollow-import-to=doctest --nofollow-import-to=IPython --output-filename=freeccr.exe src/main.py 