$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ResearchAwake {
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern uint SetThreadExecutionState(uint flags);
}
'@
$previousState = [ResearchAwake]::SetThreadExecutionState([UInt32]2147483649)
[ordered]@{captured_utc=[DateTime]::UtcNow.ToString('o'); api='SetThreadExecutionState'; requested='ES_CONTINUOUS | ES_SYSTEM_REQUIRED'; return_value=$previousState; duration='Parent Windows PowerShell thread waits synchronously for native stages; cleared on completion'} | ConvertTo-Json | Set-Content -Encoding utf8 -LiteralPath (Join-Path $PSScriptRoot 'native-awake-request.json')
try {
  & wsl.exe -d Ubuntu-24.04 -u root --exec bash /mnt/c/Users/somam/Documents/Codex/2026-11-06/read-windows-second-machine-handoff-txt/work/run-native-stages.sh
  $nativeExit = $LASTEXITCODE
} finally {
  [ResearchAwake]::SetThreadExecutionState([UInt32]2147483648) | Out-Null
}
exit $nativeExit
