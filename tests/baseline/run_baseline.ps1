# Phase 1 acceptance baseline: byte-for-byte comparison of zero-cost CLI commands
# Usage:
#   .\run_baseline.ps1 capture   # capture before refactor
#   .\run_baseline.ps1 compare   # compare after refactor (exit 0 = all match)
# NOTE: keep this file pure ASCII. PowerShell 5.1 reads BOM-less files as ANSI/GBK,
# and non-ASCII comments corrupt the parse (bytes swallow line breaks).
param(
    [Parameter(Position = 0)][ValidateSet("capture", "compare")][string]$Mode,
    [string]$Python = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot   # tests/
$proj = Split-Path -Parent $root            # project root
Set-Location $proj

# Zero-cost smoke commands (existing data triggers skip paths / read-only commands; no LLM calls)
# format_fixture uses isolated date 2000-01-01 with a frozen draft md.
# Rationale: the 2026-08-11 draft is NOT stable - interaction tests fire async LLM
# write jobs that keep rewriting the draft AFTER the test suite exits.
$COMMANDS = @(
    @{ name = "help";          args = @("--help") },
    @{ name = "status_0811";   args = @("status", "--date", "2026-08-11") },
    @{ name = "status_0829";   args = @("status", "--date", "2026-08-29") },
    @{ name = "redline_text";  args = @("redline", "--file", "tests\baseline\redline_sample.txt") },
    @{ name = "collect_skip";  args = @("collect", "--date", "2026-08-29") },
    @{ name = "score_skip";    args = @("score", "--date", "2026-08-29") },
    @{ name = "notify_skip";   args = @("notify", "--date", "2026-08-29") },
    @{ name = "sync_skip";     args = @("sync", "--date", "2026-08-29") },
    @{ name = "choose_noargs"; args = @("choose") },
    @{ name = "write_noargs";  args = @("write") },
    @{ name = "format_fixture"; args = @("format", "--date", "2000-01-01") },
    @{ name = "feedback_dedup"; args = @("feedback-dedup") }
)

# format_fixture: copy the frozen draft in before running, clean up (md + rendered html) after.
$FIXTURE_SRC = Join-Path $PSScriptRoot "format_fixture_v1.md"
$FIXTURE_MD  = Join-Path $proj "data\drafts\2000-01-01_v1.md"
$FIXTURE_HTML = Join-Path $proj "data\drafts\2000-01-01.html"
Copy-Item $FIXTURE_SRC $FIXTURE_MD -Force

function Invoke-Cmd {
    param($name, $argList, $suffix)
    $out = Join-Path $PSScriptRoot "$name`_$suffix.out.txt"
    $err = Join-Path $PSScriptRoot "$name`_$suffix.err.txt"
    $p = Start-Process -FilePath $Python -ArgumentList (@("cat_shop.py") + $argList) `
        -RedirectStandardOutput $out -RedirectStandardError $err `
        -NoNewWindow -Wait -PassThru
    return @{ exit = $p.ExitCode; out = $out; err = $err }
}

if ($Mode -eq "capture") {
    foreach ($c in $COMMANDS) {
        $r = Invoke-Cmd $c.name $c.args "base"
        "{0,-16} captured (exit={1})" -f $c.name, $r.exit
    }
    "Baseline saved to $PSScriptRoot"
    Remove-Item $FIXTURE_MD, $FIXTURE_HTML -ErrorAction SilentlyContinue
    exit 0
}

# ---- compare ----
# redline output embeds a wall-clock "ts" field; normalize it before comparing
function Get-NormalizedHash {
    param($file)
    $content = Get-Content $file -Raw
    $content = $content -replace '"ts":\s*"\d{4}-\d{2}-\d{2}T[\d:]+"', '"ts": "<TS>"'
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($content)
    $md5 = [System.Security.Cryptography.MD5]::Create()
    ([System.BitConverter]::ToString($md5.ComputeHash($bytes))).Replace("-", "")
}

$fail = 0
foreach ($c in $COMMANDS) {
    $r = Invoke-Cmd $c.name $c.args "new"
    $ok = $true
    foreach ($ext in @("out", "err")) {
        $baseF = Join-Path $PSScriptRoot "$($c.name)_base.$ext.txt"
        $newF  = Join-Path $PSScriptRoot "$($c.name)_new.$ext.txt"
        if (-not (Test-Path $baseF)) { "$($c.name): missing baseline file $ext"; $ok = $false; continue }
        $h1 = Get-NormalizedHash $baseF
        $h2 = Get-NormalizedHash $newF
        if ($h1 -ne $h2) {
            $ok = $false
            "[FAIL] {0} . {1} mismatch" -f $c.name, $ext
            if ($VerbosePreference -ne 'SilentlyContinue') {
                Compare-Object (Get-Content $baseF) (Get-Content $newF) |
                    Select-Object -First 10 | ForEach-Object { "   diff: $($_.SideIndicator) $($_.InputObject)" }
            }
        }
    }
    if ($ok) { "[PASS] {0}" -f $c.name } else { $fail++ }
}
""
if ($fail -eq 0) { "All 12 commands byte-identical - PASS"; $code = 0 }
else { "$fail command(s) mismatch - FAIL"; $code = 1 }
Remove-Item $FIXTURE_MD, $FIXTURE_HTML -ErrorAction SilentlyContinue
exit $code
