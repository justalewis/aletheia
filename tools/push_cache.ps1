<#
.SYNOPSIS
Seed the content cache on the Fly volume from a local backend/content_cache.

.DESCRIPTION
A fresh `warm_content` on the server takes a couple of hours. This copies a cache you already
have instead: it tars the local folder, uploads the archive to the volume with
`flyctl ssh sftp put`, and unpacks it into /data/content_cache with `flyctl ssh console -C`.

Files in the archive overwrite the same files on the volume; anything else already there
stays, unless you pass -Replace. While it runs, the script requests /healthz every 20 seconds
so Fly's auto-stop doesn't stop the machine mid-upload.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File tools\push_cache.ps1
.EXAMPLE
powershell -ExecutionPolicy Bypass -File tools\push_cache.ps1 -CachePath "D:\aletheia\content_cache" -DryRun
#>
param(
    # The cache to upload. Defaults to backend\content_cache next to this script.
    [string]$CachePath = (Join-Path $PSScriptRoot "..\backend\content_cache"),
    [string]$App = "aletheia-oer",
    # Target one machine by id (each machine has its own volume). Default: the app's running machine.
    [string]$Machine = "",
    # Delete /data/content_cache on the volume before unpacking.
    [switch]$Replace,
    # Build the archive and print the flyctl commands without running them.
    [switch]$DryRun,
    [string]$Flyctl = ""
)

$ErrorActionPreference = "Stop"
$env:FLY_NO_UPDATE_CHECK = "1"
$RemoteDir = "/data/content_cache"

function Find-Flyctl {
    if ($Flyctl) { return $Flyctl }
    $cmd = Get-Command flyctl -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $default = Join-Path $HOME ".fly\bin\flyctl.exe"
    if (Test-Path $default) { return $default }
    throw "Can't find flyctl. Install it (https://fly.io/docs/flyctl/install/) or pass -Flyctl <path>."
}

function Invoke-Fly([string[]]$FlyArgs) {
    $target = @("-a", $App)
    if ($Machine) { $target += @("--machine", $Machine) }
    $all = $FlyArgs + $target
    Write-Host ("> flyctl " + ($all -join " "))
    if ($DryRun) { return }
    & $script:fly @all
    if ($LASTEXITCODE -ne 0) { throw "flyctl exited with code $LASTEXITCODE" }
}

# ---- what we're sending
if (-not (Test-Path $CachePath -PathType Container)) { throw "No cache folder at $CachePath" }
$CachePath = (Resolve-Path $CachePath).Path
$files = Get-ChildItem $CachePath -Recurse -File
$entries = @(Get-ChildItem $CachePath -Directory | Where-Object { Test-Path (Join-Path $_.FullName "meta.json") })
if ($files.Count -eq 0) { throw "$CachePath is empty. Run manage.py warm_content first." }
$mb = ($files | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("{0}: {1} readings, {2} files, {3:N1} MB" -f $CachePath, $entries.Count, $files.Count, $mb)

$fly = Find-Flyctl
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$archive = Join-Path ([IO.Path]::GetTempPath()) "aletheia-cache-$stamp.tgz"
$remoteArchive = "/data/aletheia-cache-$stamp.tgz"

# ---- pack (tar.exe ships with Windows 10 and later)
Write-Host "Packing $archive"
& tar.exe -czf $archive -C $CachePath .
if ($LASTEXITCODE -ne 0) { throw "tar exited with code $LASTEXITCODE" }
Write-Host ("Archive: {0:N1} MB" -f ((Get-Item $archive).Length / 1MB))

$keepAwake = $null
try {
    if (-not $DryRun) {
        # Wake the machine through the proxy and keep it awake; ssh sessions don't count as traffic.
        $url = "https://$App.fly.dev/healthz"
        $up = $false
        for ($i = 0; $i -lt 30 -and -not $up; $i++) {
            try { $up = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 $url).StatusCode -eq 200 } catch { Start-Sleep -Seconds 2 }
        }
        if (-not $up) { throw "$url didn't answer; is the app deployed?" }
        $keepAwake = Start-Job -ArgumentList $url -ScriptBlock {
            param($u)
            while ($true) {
                try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 $u | Out-Null } catch { }
                Start-Sleep -Seconds 20
            }
        }
    }

    Invoke-Fly @("ssh", "console", "-C", "rm -f $remoteArchive")
    Invoke-Fly @("ssh", "sftp", "put", $archive, $remoteArchive)

    $steps = @()
    if ($Replace) { $steps += "rm -rf $RemoteDir" }
    $steps += "mkdir -p $RemoteDir"
    $steps += "tar -xzf $remoteArchive -C $RemoteDir"
    $steps += "rm -f $remoteArchive"
    $steps += "echo files on the volume: `$(find $RemoteDir -type f | wc -l)"
    Invoke-Fly @("ssh", "console", "-C", ("sh -c '" + ($steps -join " && ") + "'"))
    Write-Host ("Sent {0} files. The count above can be higher if the volume already had other readings." -f $files.Count)
}
finally {
    if ($keepAwake) { Stop-Job $keepAwake; Remove-Job $keepAwake }
    Remove-Item $archive -ErrorAction SilentlyContinue
}
