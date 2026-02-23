$ErrorActionPreference = "Continue"

$RAPID_HOST = $env:RAPID_CONSUMER_HOST
$RAPID_KEY  = $env:RAPID_CONSUMER_KEY

if (-not $RAPID_HOST) { Write-Host "STOP: RAPID_CONSUMER_HOST not set"; exit 1 }
if (-not $RAPID_KEY)  { Write-Host "STOP: RAPID_CONSUMER_KEY not set";  exit 1 }

Write-Host "RAPID_CONSUMER_HOST length: $($RAPID_HOST.Length)"
Write-Host "RAPID_CONSUMER_KEY  length: $($RAPID_KEY.Length)"
Write-Host ""

$PASS = 0
$FAIL = 0

# NOTE: C1 (/.well-known/openapi-demo.json) skipped via Rapid Runtime
# Reason: RapidAPI only proxies paths defined in the imported spec.
#         This meta-endpoint is intentionally excluded from the spec.
#         Verified directly at api.decisionproof.io.kr (PASS in live smoke).
Write-Host "=== C1: openapi-demo ==="
Write-Host "SKIP (not in RapidAPI spec by design — verified directly at api.decisionproof.io.kr)"
Write-Host ""

# C2-1: Create
Write-Host "=== C2-1: POST /v1/demo/runs ==="
$uri2 = "https://$RAPID_HOST/v1/demo/runs"
$body2 = '{"inputs":{"question":"Give me 3 bullets about Decisionproof Mini Demo."}}'
$RUN_ID = $null
try {
    $r2 = Invoke-WebRequest -Uri $uri2 `
        -Method POST `
        -Headers @{ "X-RapidAPI-Key" = $RAPID_KEY; "X-RapidAPI-Host" = $RAPID_HOST; "Content-Type" = "application/json" } `
        -Body $body2 `
        -UseBasicParsing -ErrorAction Stop
    Write-Host "HTTP $($r2.StatusCode)"
    $aiH = $r2.Headers.Keys | Where-Object { $_ -match "^x-dp-ai" }
    if ($aiH) { Write-Host "x-dp-ai-* headers: $($aiH -join ', ')" } else { Write-Host "x-dp-ai-* headers: (none)" }
    $p2 = $r2.Content | ConvertFrom-Json
    $RUN_ID = $p2.run_id
    if ($r2.StatusCode -in 200,202) { $PASS++; Write-Host "PASS: HTTP $($r2.StatusCode)" } else { $FAIL++; Write-Host "FAIL: HTTP $($r2.StatusCode)" }
    if ($RUN_ID) { $PASS++; Write-Host "PASS: run_id=$RUN_ID" } else { $FAIL++; Write-Host "FAIL: run_id missing"; exit 1 }
} catch {
    $FAIL++
    $sc = $_.Exception.Response.StatusCode.value__
    Write-Host "HTTP $sc"
    Write-Host "FAIL: $($_.Exception.Message)"
    if ($sc -in 401,403) { Write-Host "STOP: Auth failure (RapidAPI Key or Proxy Secret)" }
    if ($sc -eq 503)     { Write-Host "STOP: 503 - RAPIDAPI_PROXY_SECRET mismatch (Fail-Closed)" }
    exit 1
}

Write-Host ""

# C2-2: Poll 1
Write-Host "=== C2-2: GET poll 1 ==="
$uri3 = "https://$RAPID_HOST/v1/demo/runs/$RUN_ID"
try {
    $r3 = Invoke-WebRequest -Uri $uri3 `
        -Headers @{ "X-RapidAPI-Key" = $RAPID_KEY; "X-RapidAPI-Host" = $RAPID_HOST } `
        -UseBasicParsing -ErrorAction Stop
    Write-Host "HTTP $($r3.StatusCode)"
    $p3 = $r3.Content | ConvertFrom-Json
    Write-Host "run status: $($p3.status)"
    if ($r3.StatusCode -eq 200) { $PASS++; Write-Host "PASS: HTTP 200" } else { $FAIL++; Write-Host "FAIL: HTTP $($r3.StatusCode)" }
} catch {
    $FAIL++
    Write-Host "FAIL: $($_.Exception.Message)"
}

Write-Host ""

# C2-3: Poll 2 (immediate) -> 429
Write-Host "=== C2-3: GET poll 2 (immediate) -> 429 ==="
try {
    $req4 = [System.Net.HttpWebRequest]::Create($uri3)
    $req4.Method = "GET"
    $req4.Headers.Add("X-RapidAPI-Key", $RAPID_KEY)
    $req4.Headers.Add("X-RapidAPI-Host", $RAPID_HOST)

    $resp4 = $null
    try {
        $resp4 = $req4.GetResponse()
    } catch [System.Net.WebException] {
        $resp4 = $_.Exception.Response
    }

    $sc4   = [int]$resp4.StatusCode
    $ra4   = $resp4.Headers["Retry-After"]
    $ct4   = $resp4.Headers["Content-Type"]
    $stream4 = $resp4.GetResponseStream()
    $reader4 = [System.IO.StreamReader]::new($stream4)
    $raw4    = $reader4.ReadToEnd()
    $reader4.Close(); $resp4.Close()

    Write-Host "HTTP $sc4"
    Write-Host "Retry-After: $(if ($ra4) { $ra4 } else { 'MISSING' })"
    Write-Host "Content-Type: $ct4"

    if ($sc4 -eq 429) { $PASS++; Write-Host "PASS: 429" } else { $FAIL++; Write-Host "FAIL: HTTP $sc4 (expected 429)" }
    if ($ra4)  { $PASS++; Write-Host "PASS: Retry-After=$ra4" } else { $FAIL++; Write-Host "FAIL: Retry-After missing" }
    if ($ct4 -match "problem\+json") { $PASS++; Write-Host "PASS: problem+json" } else { $FAIL++; Write-Host "FAIL: CT=$ct4" }

    $p4 = $raw4 | ConvertFrom-Json
    Write-Host "detail: $($p4.detail)"
    if ($p4.instance) { $PASS++; Write-Host "PASS: instance present" } else { $FAIL++; Write-Host "FAIL: instance missing" }
    if ($p4.detail)   { $PASS++; Write-Host "PASS: detail present"   } else { $FAIL++; Write-Host "FAIL: detail missing" }
} catch {
    $FAIL++
    Write-Host "FAIL: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "=============================="
Write-Host "RAPID RUNTIME SMOKE RESULT"
Write-Host "PASS: $PASS  FAIL: $FAIL"
if ($FAIL -eq 0) { Write-Host "ALL PASS" } else { Write-Host "FAILED ($FAIL items)" }
Write-Host "=============================="
