# Builds dist\QuotaTray.exe and dist\QuotaWidget.exe (single files). Run from the repo root: .\build.ps1
# Stops at the first failing step.
#
# Code signing (optional): set QUOTA_SIGN_THUMBPRINT to the SHA-1 thumbprint of a code-signing
# certificate in your certificate store (and install the Windows SDK for signtool.exe). Without it
# the exes are built unsigned and Windows SmartScreen may warn when they are first run.
$ErrorActionPreference = "Stop"
function Check($step) { if ($LASTEXITCODE -ne 0) { throw "$step failed (exit $LASTEXITCODE)" } }
$dist = Join-Path $PSScriptRoot "dist"
foreach ($name in "QuotaTray", "QuotaWidget") {   # only copies running from this dist\ lock the output files
    if (Get-Process $name -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$dist\*" }) {
        throw "dist\$name.exe is running; close it first"
    }
}
if (-not (Test-Path .venv)) {   # Python 3.12 from the py launcher (python.org), else from uv
    $hasPy = $false
    if (Get-Command py -ErrorAction SilentlyContinue) { py -3.12 -c "pass" 2>$null; $hasPy = $LASTEXITCODE -eq 0 }
    if ($hasPy) { py -3.12 -m venv .venv }
    elseif (Get-Command uv -ErrorAction SilentlyContinue) { uv venv --seed --python 3.12 .venv }
    else { throw "Python 3.12 not found. Install it from python.org (with the py launcher) or with uv" }
    Check 'venv'
}
.\.venv\Scripts\python -m pip install -q -r requirements-dev.txt; Check 'pip install'
.\.venv\Scripts\python -m pytest -q; Check 'tests'
.\.venv\Scripts\python -c "from quota_tray.icon import save_ico; save_ico('quota_tray.ico')"; Check 'icon'

# Windows version resource (shown in file properties and Apps & features)
$version = (.\.venv\Scripts\python -c "import quota_core; print(quota_core.__version__)").Trim(); Check 'version'
$parts = ($version.Split('.') + @('0', '0', '0'))[0..3] -join ', '
foreach ($name in "QuotaTray", "QuotaWidget") {
    @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($parts), prodvers=($parts)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Quota Tray'),
    StringStruct('FileDescription', '$name - LLM subscription quota'),
    StringStruct('FileVersion', '$version'),
    StringStruct('ProductName', 'Quota Tray'),
    StringStruct('ProductVersion', '$version'),
    StringStruct('OriginalFilename', '$name.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"@ | Set-Content -Encoding utf8 "version_$name.txt"
}

$common = @(
    '--noconfirm', '--clean', '--onefile', '--windowed', '--icon', 'quota_tray.ico',
    '--exclude-module', 'PySide6.QtNetwork', '--exclude-module', 'PySide6.QtQml',
    '--exclude-module', 'PySide6.QtQuick', '--exclude-module', 'PySide6.QtSql',
    '--exclude-module', 'PySide6.QtTest', '--exclude-module', 'PySide6.QtOpenGL',
    '--exclude-module', 'tkinter', '--exclude-module', 'pytest'
)
.\.venv\Scripts\pyinstaller @common --name QuotaTray --version-file version_QuotaTray.txt --hidden-import pystray._win32 run_tray.pyw
Check 'pyinstaller QuotaTray'
.\.venv\Scripts\pyinstaller @common --name QuotaWidget --version-file version_QuotaWidget.txt --exclude-module pystray run_widget.pyw
Check 'pyinstaller QuotaWidget'

if ($env:QUOTA_SIGN_THUMBPRINT) {
    $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $signtool) { throw "QUOTA_SIGN_THUMBPRINT is set but signtool.exe was not found (install the Windows SDK)" }
    & $signtool.FullName sign /sha1 $env:QUOTA_SIGN_THUMBPRINT /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
        dist\QuotaTray.exe dist\QuotaWidget.exe
    Check 'signing'
    Write-Host "Signed both exes"
} else {
    Write-Host "Not signed (set QUOTA_SIGN_THUMBPRINT to a code-signing certificate thumbprint to sign)"
}

# Release package: both exes plus the license texts that must travel with them (Qt/PySide6 and pystray
# are LGPL-3.0; see THIRD_PARTY_NOTICES.md), then SHA-256 checksums of everything published.
$zip = "dist\QuotaTray-$version-win-x64.zip"
$stage = "build\package"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory $stage | Out-Null
Copy-Item dist\QuotaTray.exe, dist\QuotaWidget.exe, LICENSE, THIRD_PARTY_NOTICES.md, README.md $stage
Copy-Item LICENSES $stage -Recurse
Compress-Archive "$stage\*" $zip -Force
Get-FileHash dist\QuotaTray.exe, dist\QuotaWidget.exe, $zip -Algorithm SHA256 |
    ForEach-Object { "{0}  {1}" -f $_.Hash.ToLower(), (Split-Path $_.Path -Leaf) } |
    Set-Content -Encoding ascii dist\SHA256SUMS.txt
Write-Host "Built dist\QuotaTray.exe, dist\QuotaWidget.exe and $zip ($version); checksums in dist\SHA256SUMS.txt"
