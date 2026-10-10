# Runs the whole pipeline in order (about 15-20 min with Ollama running).
# Usage (PowerShell, in this folder):  powershell -ExecutionPolicy Bypass -File .\run_all.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"   # avoid console encoding errors on Korean/arrow characters
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Step($title, $cmd) {
    Write-Host "`n==== $title ====" -ForegroundColor Cyan
    $sw = [Diagnostics.Stopwatch]::StartNew()
    Invoke-Expression $cmd
    if ($LASTEXITCODE -ne 0) { throw "Failed: $title" }
    Write-Host ("  done in {0:N0}s" -f $sw.Elapsed.TotalSeconds) -ForegroundColor DarkGray
}

Step "0. sample data"            "python make_sample_data.py"
Step "1. KPI + confusion"        "python step1_kpi_analysis.py"
Step "2a. root cause (7b)"       "python step2_root_cause_llm.py --model qwen2.5:7b"
Step "2b. root cause (3b)"       "python step2_root_cause_llm.py --model qwen2.5:3b"
Step "3. model comparison"       "python step3_compare_models.py"
Step "4. one-pager (week 5)"     "python step4_onepager.py --week 5"
Step "4. one-pager (week 8)"     "python step4_onepager.py --week 8"
Step "5. results summary"        "python make_results_md.py"

Write-Host "`nAll done. Open output\onepager_week5.html and RESULTS.md" -ForegroundColor Green
