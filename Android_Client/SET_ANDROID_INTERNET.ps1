$ErrorActionPreference = 'Stop'
$manifest='android\app\src\main\AndroidManifest.xml'
if (-not (Test-Path $manifest)) { throw 'Android manifest missing. Run flutter create first.' }
$content=Get-Content $manifest -Raw
if ($content -notmatch 'android.permission.INTERNET') {
    $content=$content.Replace('<application', '<uses-permission android:name="android.permission.INTERNET" />' + "`r`n    " + '<application')
    Set-Content -LiteralPath $manifest -Value $content -Encoding UTF8
}
