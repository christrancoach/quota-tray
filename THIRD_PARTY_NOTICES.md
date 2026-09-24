# Third-party notices

Quota Tray's own source code is MIT licensed (see [LICENSE](LICENSE)). The prebuilt `QuotaTray.exe`
and `QuotaWidget.exe` also contain third-party software under its own licenses, listed below.
Full license texts are in [LICENSES/](LICENSES) and in the release zip.

## Code and data this project uses

| Component | How it's used | License |
|---|---|---|
| [quse](https://github.com/alexeygrigorev/quse) by Alexey Grigorev | The Grok weekly-percentage logic in `quota_core/providers/grok.py` is ported from it: prefer the GrokBuild entry in `productUsage`, then `creditUsagePercent`, then `onDemandUsed / onDemandCap` | MIT ([LICENSES/quse.txt](LICENSES/quse.txt)) |
| [LiteLLM](https://github.com/BerriAI/litellm) model price list (`model_prices_and_context_window.json`) | Downloaded at runtime, at most weekly, to estimate costs in the usage report. It is **not** included in the source or the exes | MIT, Copyright (c) 2023 Berri AI (everything outside LiteLLM's `enterprise/` directory, which includes this file) |

quse's package metadata declares MIT, but its repository has no LICENSE file or copyright line.
The notice in `LICENSES/quse.txt` credits the author named in that metadata.

## Bundled in the exes

Versions are the ones pinned in `requirements.txt` and used for the v1.2.0 release build.

| Component | Version | In | License | Text |
|---|---|---|---|---|
| Python (CPython, python-build-standalone build) | 3.12.13 | both | PSF-2.0 | [Python.txt](LICENSES/Python.txt), [Python-runtime-extras.txt](LICENSES/Python-runtime-extras.txt) |
| ↳ OpenSSL (`libssl-3-x64.dll`, `libcrypto-3-x64.dll`) | 3.5.7 | both | Apache-2.0 | [Apache-2.0.txt](LICENSES/Apache-2.0.txt) |
| ↳ SQLite (`sqlite3.dll`) | 3.53.1 | both | Public domain | none needed |
| ↳ libffi, expat, zlib, libmpdec | as shipped with Python 3.12.13 | both | MIT / MIT / zlib / BSD-2-Clause | [Python.txt](LICENSES/Python.txt) |
| ↳ bzip2 | as shipped with Python 3.12.13 | both | bzip2 license (BSD-style) | [Python-runtime-extras.txt](LICENSES/Python-runtime-extras.txt) |
| ↳ xz (liblzma) | as shipped with Python 3.12.13 | both | 0BSD | none needed |
| **PySide6-Essentials** (Qt for Python) | 6.11.2 | both | **LGPL-3.0-only** (offered as LGPL-3.0 / GPL-2.0 / GPL-3.0; used under LGPL-3.0) | [LGPL-3.0.txt](LICENSES/LGPL-3.0.txt), [GPL-3.0.txt](LICENSES/GPL-3.0.txt) |
| **shiboken6** | 6.11.2 | both | **LGPL-3.0-only** (same choice) | same |
| **Qt** libraries shipped in the PySide6 wheel: Qt6Core, Qt6Gui, Qt6Widgets, Qt6Svg, Qt6Network and the platform, style, icon-engine and image-format plugins | 6.11.2 | both | **LGPL-3.0-only** | same; Qt's own third-party components (PCRE2, HarfBuzz, FreeType, libpng, libjpeg, zlib, md4c and others) are listed in [Licenses Used in Qt 6](https://doc.qt.io/qt-6/licenses-used-in-qt.html) |
| ↳ `opengl32sw.dll` (Mesa llvmpipe software OpenGL fallback, from the PySide6 wheel) | as shipped with PySide6 6.11.2 | both | MIT | https://docs.mesa3d.org/license.html |
| **pystray** | 0.19.5 | QuotaTray.exe only | **LGPL-3.0** | [LGPL-3.0.txt](LICENSES/LGPL-3.0.txt), [GPL-3.0.txt](LICENSES/GPL-3.0.txt) |
| six (a pystray dependency) | 1.17.0 | QuotaTray.exe only | MIT | [six.txt](LICENSES/six.txt) |
| Pillow, with its bundled libjpeg-turbo, libpng, zlib, FreeType, HarfBuzz, libwebp, libtiff, lcms2, libavif and others | 12.3.0 | QuotaTray.exe only | MIT-CMU (HPND); bundled libraries under their own licenses | [Pillow.txt](LICENSES/Pillow.txt) (covers all of them) |
| Microsoft Visual C++ runtime (`VCRUNTIME140*.dll`, `MSVCP140*.dll`, shipped with Python and in the PySide6/shiboken6 wheels) | 14.x | both | Microsoft Visual C++ Redistributable terms | redistributable under the Visual Studio license terms |
| PyInstaller bootloader | 6.22.3 | both | GPL-2.0-or-later **with the bootloader exception** | The exception lets you distribute the built exe under any license. See https://pyinstaller.org/en/stable/license.html |

**Build and test tools only, not in the exes:** PyInstaller (GPL-2.0-or-later with exception),
pyinstaller-hooks-contrib (GPL-2.0 / Apache-2.0), altgraph (MIT), pefile (MIT), packaging
(Apache-2.0 or BSD-2-Clause), pywin32-ctypes (BSD-3-Clause), pytest (MIT), pluggy (MIT), iniconfig
(MIT), colorama (BSD-3-Clause), Pygments (BSD-2-Clause), setuptools (MIT), pip (MIT).

## How the exes comply with the LGPL (Qt, PySide6, shiboken6, pystray)

The exes are built with PyInstaller in `--onefile` mode. The Qt, PySide6 and shiboken6 DLLs and
pystray's Python modules are stored unmodified inside each exe and unpacked to a temporary folder
when it starts. They are loaded dynamically at runtime; nothing is statically linked. Quota Tray
does not modify any LGPL component.

Under LGPL-3.0 section 4 (Combined Works), this project:

1. **(a) Gives prominent notice** that these libraries are used and are covered by the LGPL: this
   file, which ships in the release zip next to the exes and in the source repository.
2. **(b) Includes copies of the GNU GPL v3 and LGPL v3:** `LICENSES/GPL-3.0.txt` and
   `LICENSES/LGPL-3.0.txt`, in the release zip and the repository.
3. **(d)(0) Provides the Corresponding Application Code**, so you can rebuild the application with a
   modified version of any LGPL library. The complete source of Quota Tray and its build script are
   published under the MIT license. To use a modified library:
   - Install your modified PySide6, shiboken6, Qt build or pystray into the build environment,
     e.g. `.\.venv\Scripts\pip install <your wheel>`.
   - Run `.\build.ps1`. PyInstaller bundles whatever version is installed, so the new exes
     use your modified library.
   - Or skip the exe entirely and run from source (`pythonw -m quota_tray` / `-m quota_widget`)
     against any compatible library version.
4. **Corresponding Source of the libraries themselves** (GPL-3.0 section 6, via LGPL-3.0). The
   unmodified source archives for the exact versions bundled are **attached to each GitHub release**
   next to the exes, with their SHA-256 checksums in `SOURCES-SHA256.txt`:
   - Qt 6.11.2 modules in the exes: `qtbase-everywhere-src-6.11.2.tar.xz` (Core, Gui, Widgets, Network,
     platform/style/gif/ico/jpeg plugins), `qtsvg-everywhere-src-6.11.2.tar.xz` (Svg and the SVG
     plugins) and `qtimageformats-everywhere-src-6.11.2.tar.xz` (icns, tga, tiff, wbmp, webp plugins),
     from https://download.qt.io/official_releases/qt/6.11/6.11.2/submodules/
   - PySide6 and shiboken6 6.11.2: `pyside-setup-everywhere-src-6.11.2.tar.xz`, from
     https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/
   - pystray 0.19.5: `pystray-0.19.5-py2.py3-none-any.whl` (pure Python, so the wheel is the source;
     PyPI publishes no sdist for this version) and `pystray-0.19.5-github-tag.tar.gz` (the v0.19.5 tag
     of https://github.com/moses-palmer/pystray)

   If an archive is ever missing from a release, open an issue and the maintainer will provide it.

Section 4(e) (Installation Information) applies only to "User Products" as defined in GPL-3.0,
i.e. consumer hardware, so it does not apply to software distributed on its own like this.

**What this means in practice:** you can use, copy and share the exes freely. If you redistribute
them, include this file and the `LICENSES/` folder, as the release zip does.
