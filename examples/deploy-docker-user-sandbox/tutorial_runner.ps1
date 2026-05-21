
# Docker Sandbox API - Tutorial Runner
# Run this script in a PowerShell window (separate from the uvicorn window)

$BaseUrl = "http://127.0.0.1:18080"

function Invoke-Api {
    param(
        [string]$Method,
        [string]$Path,
        [string]$Body = "",
        [string]$Token = ""
    )
    $headers = @{ "Content-Type" = "application/json" }
    if ($Token) { $headers["Authorization"] = "Bearer $Token" }
    $uri = "$BaseUrl$Path"
    if ($Body) {
        $resp = Invoke-RestMethod -Method $Method -Uri $uri -Headers $headers -Body $Body -ErrorAction Stop
    } else {
        $resp = Invoke-RestMethod -Method $Method -Uri $uri -Headers $headers -ErrorAction Stop
    }
    return $resp
}

Write-Host "=== Step 6: Register Alice ===" -ForegroundColor Cyan
$Alice = Invoke-Api -Method POST -Path "/api/v1/auth/register" -Body '{"username":"alice-tutorial","password":"pass-12345"}'
Write-Host "Alice registered: id=$($Alice.id)  username=$($Alice.username)"

Write-Host "`n=== Step 7: Alice Login ===" -ForegroundColor Cyan
$AliceLogin = Invoke-Api -Method POST -Path "/api/v1/auth/login" -Body '{"username":"alice-tutorial","password":"pass-12345"}'
$AliceToken = $AliceLogin.access_token
Write-Host "Alice token length: $($AliceToken.Length)"

Write-Host "`n=== Step 8: Create Assistant ===" -ForegroundColor Cyan
$AssistantId = "minimax-coder-tutorial"
$AssistantBody = @{
    id       = $AssistantId
    name     = "Minimax Coder Tutorial"
    model    = "anthropic:MiniMax-M2.7-highspeed"
    image    = "python:3.12-slim"
    base_dir = "/workspace"
} | ConvertTo-Json -Compress
$Assistant = Invoke-Api -Method POST -Path "/api/v1/assistants" -Body $AssistantBody -Token $AliceToken
Write-Host "Assistant created: id=$($Assistant.id)"

Write-Host "`n=== Step 9: Create Alice Thread 1 ===" -ForegroundColor Cyan
$T1Body = @{ assistant_id = $AssistantId; name = "alice-thread-1" } | ConvertTo-Json -Compress
$AliceThread1 = Invoke-Api -Method POST -Path "/api/v1/threads" -Body $T1Body -Token $AliceToken
$AliceThreadId1 = $AliceThread1.thread_id
Write-Host "Thread 1 created: $AliceThreadId1"

Write-Host "`n=== Step 10: Alice Normal Chat (model reply) ===" -ForegroundColor Cyan
$Chat1 = Invoke-Api -Method POST -Path "/api/v1/threads/$AliceThreadId1/chat" `
    -Body '{"message":"Reply with exactly: api-agent-ok"}' -Token $AliceToken
Write-Host "Response: $($Chat1.response)"
Write-Host "Container: $($Chat1.container_id)"

Write-Host "`n=== Step 11: Alice Sandbox Execution ===" -ForegroundColor Cyan
$RunBody = '{"message":"run: python --version && echo alice-secret-data > /workspace/shared.txt && cat /workspace/shared.txt"}'
$AliceRun1 = Invoke-Api -Method POST -Path "/api/v1/threads/$AliceThreadId1/chat" -Body $RunBody -Token $AliceToken
Write-Host "Response: $($AliceRun1.response)"
$AliceContainerId = $AliceRun1.container_id
Write-Host "Alice container: $AliceContainerId"

Write-Host "`n=== Step 12: Create Alice Thread 2 ===" -ForegroundColor Cyan
$T2Body = @{ assistant_id = $AssistantId; name = "alice-thread-2" } | ConvertTo-Json -Compress
$AliceThread2 = Invoke-Api -Method POST -Path "/api/v1/threads" -Body $T2Body -Token $AliceToken
$AliceThreadId2 = $AliceThread2.thread_id
Write-Host "Thread 2 created: $AliceThreadId2"

Write-Host "`n=== Step 13: Verify Alice Reuses Same Sandbox Across Threads ===" -ForegroundColor Cyan
$AliceRun2 = Invoke-Api -Method POST -Path "/api/v1/threads/$AliceThreadId2/chat" `
    -Body '{"message":"run: cat /workspace/shared.txt"}' -Token $AliceToken
Write-Host "Response: $($AliceRun2.response)"
Write-Host "Same container? $($AliceRun2.container_id -eq $AliceContainerId)"

Write-Host "`n=== Step 14: Register Bob ===" -ForegroundColor Cyan
$Bob = Invoke-Api -Method POST -Path "/api/v1/auth/register" -Body '{"username":"bob-tutorial","password":"pass-12345"}'
Write-Host "Bob registered: id=$($Bob.id)"
$BobLogin = Invoke-Api -Method POST -Path "/api/v1/auth/login" -Body '{"username":"bob-tutorial","password":"pass-12345"}'
$BobToken = $BobLogin.access_token
Write-Host "Bob token length: $($BobToken.Length)"

Write-Host "`n=== Step 15: Bob Creates Thread ===" -ForegroundColor Cyan
$BT1Body = @{ assistant_id = $AssistantId; name = "bob-thread-1" } | ConvertTo-Json -Compress
$BobThread1 = Invoke-Api -Method POST -Path "/api/v1/threads" -Body $BT1Body -Token $BobToken
$BobThreadId1 = $BobThread1.thread_id
Write-Host "Bob thread: $BobThreadId1"

Write-Host "`n=== Step 16: Verify Bob Cannot Read Alice's File ===" -ForegroundColor Cyan
$BobRun1 = Invoke-Api -Method POST -Path "/api/v1/threads/$BobThreadId1/chat" `
    -Body '{"message":"run: cat /workspace/shared.txt"}' -Token $BobToken
Write-Host "Response: $($BobRun1.response)"
$BobContainerId = $BobRun1.container_id
Write-Host "Bob container: $BobContainerId"
Write-Host "Different containers (isolation OK)? $($BobContainerId -ne $AliceContainerId)"

Write-Host "`n=== Step 17: List API-Recorded Sandboxes ===" -ForegroundColor Cyan
$Sandboxes = Invoke-Api -Method GET -Path "/api/v1/sandboxes" -Token $AliceToken
$Sandboxes | Format-Table

Write-Host "`n=== Step 18: List Real Containers on Docker Host ===" -ForegroundColor Cyan
ssh deepagents-docker "docker ps -a --filter label=deepagents.sandbox=true --format '{{.ID}} {{.Names}} {{.Status}}'"

Write-Host "`n=== Step 19: Cleanup Tutorial Containers ===" -ForegroundColor Cyan
$short1 = $AliceContainerId.Substring(0,12)
$short2 = $BobContainerId.Substring(0,12)
Write-Host "Removing Alice container: $short1"
Write-Host "Removing Bob container:   $short2"
ssh deepagents-docker "docker rm -f $AliceContainerId $BobContainerId"

Write-Host "`n=== Step 20: Verify Cleanup ===" -ForegroundColor Cyan
ssh deepagents-docker "docker ps -a --filter label=deepagents.sandbox=true"

Write-Host "`n✅ Tutorial complete! Stop the uvicorn window with Ctrl+C." -ForegroundColor Green
