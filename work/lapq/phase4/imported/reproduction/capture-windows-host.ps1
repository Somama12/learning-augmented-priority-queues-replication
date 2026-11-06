param([string]$OutputPath = (Join-Path $PSScriptRoot 'windows-host-initial.json'), [string]$Stage = 'before_measurements')
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ResearchHostInfo {
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool GetPhysicallyInstalledSystemMemory(out ulong kilobytes);
  [StructLayout(LayoutKind.Sequential)]
  public struct PowerStatus {
    public byte ACLineStatus, BatteryFlag, BatteryLifePercent, SystemStatusFlag;
    public uint BatteryLifeTime, BatteryFullLifeTime;
  }
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern bool GetSystemPowerStatus(out PowerStatus status);
}
'@
[UInt64]$installedKilobytes = 0
$memoryRead = [ResearchHostInfo]::GetPhysicallyInstalledSystemMemory([ref]$installedKilobytes)
$powerStatus = [ResearchHostInfo+PowerStatus]::new()
$powerRead = [ResearchHostInfo]::GetSystemPowerStatus([ref]$powerStatus)
$metadata = [ordered]@{
  captured_utc = [DateTime]::UtcNow.ToString('o')
  execution_shell = 'Native Windows PowerShell via exec_command; not WSL'
  computer_name = $env:COMPUTERNAME
  os_description = [Runtime.InteropServices.RuntimeInformation]::OSDescription
  os_architecture = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
  manufacturer = Get-ItemPropertyValue -LiteralPath 'HKLM:\HARDWARE\DESCRIPTION\System\BIOS' -Name SystemManufacturer
  model = Get-ItemPropertyValue -LiteralPath 'HKLM:\HARDWARE\DESCRIPTION\System\BIOS' -Name SystemProductName
  cpu = Get-ItemPropertyValue -LiteralPath 'HKLM:\HARDWARE\DESCRIPTION\System\CentralProcessor\0' -Name ProcessorNameString
  logical_processors_visible_to_process = [Environment]::ProcessorCount
  processor_registry_entries = @(Get-ChildItem -LiteralPath 'HKLM:\HARDWARE\DESCRIPTION\System\CentralProcessor').Count
  installed_ram_query_succeeded = $memoryRead
  installed_ram_bytes = if ($memoryRead) { $installedKilobytes * 1024 } else { $null }
  windows_display_version = Get-ItemPropertyValue -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -Name DisplayVersion
  windows_build = Get-ItemPropertyValue -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -Name CurrentBuild
  windows_ubr = Get-ItemPropertyValue -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -Name UBR
  legacy_registry_product_name = Get-ItemPropertyValue -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -Name ProductName
  power_query_succeeded = $powerRead
  ac_line_status = if ($powerRead) { [int]$powerStatus.ACLineStatus } else { $null }
  ac_line_status_key = '0=offline; 1=online; 255=unknown'
  battery_percent = if ($powerRead) { [int]$powerStatus.BatteryLifePercent } else { $null }
  battery_saver = if ($powerRead) { [int]$powerStatus.SystemStatusFlag } else { $null }
  initial_wmi_observation = 'Get-CimInstance Win32_ComputerSystem and Win32_Processor returned Access denied in execution sandbox.'
  provenance = 'Observed locally; user identifies this laptop as the second physical machine. No first-host identity reused.'
  observation_stage = $Stage
  measurements_started = ($Stage -eq 'after_measurements')
}
$metadata | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 -LiteralPath $OutputPath
$metadata | ConvertTo-Json -Depth 4
