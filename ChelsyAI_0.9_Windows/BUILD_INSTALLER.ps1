$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
Write-Host 'Chelsy AI 0.9.3 - Windows build (Host + Client installers)' -ForegroundColor Cyan
# Produces:
#   installer\ChelsyAI_Host_Setup_0.9.3.exe    (Desktop + Host + Client + tools, for the owner's laptop)
#   installer\ChelsyAI_Client_Setup_0.9.3.exe  (standalone client, no Ollama)
#   installer\SHA256SUMS.txt
# Optional Authenticode signing: set CHELSY_SIGN_PFX (path to .pfx) and CHELSY_SIGN_PASSWORD.
# Unit tests are mandatory: a failing test stops the build.

function Invoke-Checked([string]$what, [scriptblock]$cmd) {
    & $cmd
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" }
}

function Find-SignTool {
    $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $kits = "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
    if (Test-Path $kits) {
        $found = Get-ChildItem $kits -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\x64\\' } | Sort-Object FullName -Descending | Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    return $null
}

function Sign-Files([string[]]$files) {
    if (-not $env:CHELSY_SIGN_PFX) { return }
    $signtool = Find-SignTool
    if (-not $signtool) { throw 'CHELSY_SIGN_PFX is set but signtool.exe was not found (install Windows SDK).' }
    foreach ($f in $files) {
        Invoke-Checked "Signing $f" { & $signtool sign /fd SHA256 /td SHA256 /tr http://timestamp.digicert.com /f $env:CHELSY_SIGN_PFX /p $env:CHELSY_SIGN_PASSWORD $f }
    }
}

function Find-Iscc {
    $candidates = @()
    if ($env:CHELSY_ISCC_PATH) { $candidates += $env:CHELSY_ISCC_PATH }
    $candidates += @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 7\ISCC.exe", "${env:LOCALAPPDATA}\Programs\Inno Setup 7\ISCC.exe")
    $iscc = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
    if (-not $iscc) {
        $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($command) { $iscc = $command.Source }
    }
    if (-not $iscc) { throw 'Inno Setup 6/7 not found. Install it or set CHELSY_ISCC_PATH.' }
    return $iscc
}

try {
    $python = $null
    if ($env:CHELSY_PYTHON) { $python = $env:CHELSY_PYTHON }
    else {
        & py -3.11 --version
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 is required (Python Launcher: py -3.11).' }
        if (-not (Test-Path '.buildvenv\Scripts\python.exe')) { Invoke-Checked 'venv' { & py -3.11 -m venv .buildvenv } }
        $python = Join-Path $PSScriptRoot '.buildvenv\Scripts\python.exe'
    }
    Invoke-Checked 'pip upgrade' { & $python -m pip install --upgrade pip }
    Invoke-Checked 'dependencies' { & $python -m pip install -r requirements.txt }

    Write-Host 'Running unit and integration tests...' -ForegroundColor Cyan
    Invoke-Checked 'Unit tests' { & $python -m unittest discover -v }
    Invoke-Checked 'Gradio/UI preflight' { & $python preflight.py }

    $common = @('--noconfirm', '--clean', '--onedir', '--version-file', 'version_info.txt',
                '--collect-submodules', 'knowledge', '--collect-submodules', 'training')

    Invoke-Checked 'Desktop build' { & $python -m PyInstaller @common --windowed --name ChelsyAI --icon assets\chelsy.ico `
        --add-data "assets;assets" --collect-all gradio --collect-all gradio_client --collect-all safehttpx `
        --collect-all huggingface_hub --collect-all groovy --collect-all orjson launcher.py }
    Invoke-Checked 'Host build' { & $python -m PyInstaller @common --console --name ChelsyHost --icon assets\chelsy.ico host_server.py }
    Invoke-Checked 'Client build' { & $python -m PyInstaller --noconfirm --clean --onedir --version-file version_info.txt --windowed `
        --name ChelsyClient --icon assets\chelsy.ico --add-data "assets;assets" --add-data "site_supabase_public.json;." `
        --exclude-module gradio --exclude-module numpy --exclude-module pandas client.py }
    Invoke-Checked 'Config build' { & $python -m PyInstaller --noconfirm --clean --onedir --version-file version_info.txt --windowed `
        --name ChelsyConfig --icon assets\chelsy.ico --add-data "site_supabase_public.json;." configure_supabase.py }
    Invoke-Checked 'DB tool build' { & $python -m PyInstaller @common --console --name ChelsyDB migrations.py }
    Invoke-Checked 'Migration tool build' { & $python -m PyInstaller --noconfirm --clean --onedir --console --name ChelsyMigrate migrate_local.py }
    Invoke-Checked 'Upload tool build' { & $python -m PyInstaller --noconfirm --clean --onedir --console --name ChelsyUpload upload_existing_to_supabase.py }

    foreach ($exe in @('dist\ChelsyAI\ChelsyAI.exe', 'dist\ChelsyHost\ChelsyHost.exe', 'dist\ChelsyClient\ChelsyClient.exe',
                       'dist\ChelsyConfig\ChelsyConfig.exe', 'dist\ChelsyDB\ChelsyDB.exe', 'dist\ChelsyMigrate\ChelsyMigrate.exe',
                       'dist\ChelsyUpload\ChelsyUpload.exe', 'dist\ChelsyAI\_internal\assets\chelsy_logo_small.png',
                       'dist\ChelsyClient\_internal\assets\chelsy_logo_small.png', 'dist\ChelsyClient\_internal\site_supabase_public.json')) {
        if (-not (Test-Path $exe)) { throw "Missing build output: $exe" }
    }
    # The client must not contain any secret Supabase key.
    $public = Get-Content 'site_supabase_public.json' -Raw
    if ($public -match 'sb_secret_|service_role') { throw 'Secret Supabase key found in client configuration!' }

    Sign-Files @((Get-ChildItem dist -Recurse -Filter *.exe | Where-Object { $_.Name -like 'Chelsy*.exe' }).FullName)

    $iscc = Find-Iscc
    Invoke-Checked 'Host installer' { & $iscc (Join-Path $PSScriptRoot 'ChelsyAI.iss') }
    Invoke-Checked 'Client installer' { & $iscc (Join-Path $PSScriptRoot 'CLIENT_ONLY.iss') }
    $setups = @('installer\ChelsyAI_Host_Setup_0.9.3.exe', 'installer\ChelsyAI_Client_Setup_0.9.3.exe')
    Sign-Files $setups

    $lines = foreach ($s in $setups) { (Get-FileHash $s -Algorithm SHA256).Hash.ToLower() + '  ' + (Split-Path $s -Leaf) }
    Set-Content -Path 'installer\SHA256SUMS.txt' -Value $lines -Encoding ascii
    if ($env:CHELSY_SIGN_PFX) { Write-Host 'Installers are Authenticode-signed.' -ForegroundColor Green }
    else { Write-Host 'NOTE: installers are NOT code-signed (set CHELSY_SIGN_PFX to sign). SmartScreen will warn.' -ForegroundColor Yellow }
    foreach ($s in $setups) { Write-Host "SUCCESS: $PSScriptRoot\$s" -ForegroundColor Green }
}
catch {
    Write-Host ('BUILD FAILED: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
