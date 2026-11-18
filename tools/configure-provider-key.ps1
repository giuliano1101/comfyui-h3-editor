param([ValidateSet('Ark','MiniMax')][string]$Provider='Ark',[switch]$Remove)
$ErrorActionPreference='Stop'
$checkout=Split-Path -Parent $PSScriptRoot
$privateDirectory=Join-Path $checkout '.acceptance/private'
New-Item -ItemType Directory -Path $privateDirectory -Force | Out-Null
$destination=Join-Path $privateDirectory ($Provider.ToLower()+'.dpapi')
if ($Remove) {
    if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination }
    Write-Host 'Local encrypted copy removed. Revoke the key in the provider console separately if needed.'
    return
}
$key=Read-Host "Enter $Provider API key locally (hidden; never paste into chat)" -AsSecureString
if ($key.Length -lt 8) { throw 'No usable key entered; nothing saved.' }
# Windows current-user DPAPI. Plaintext is never written to a file or printed.
$encrypted=ConvertFrom-SecureString -SecureString $key
Set-Content -LiteralPath $destination -Value $encrypted -Encoding ascii
$identity=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$acl=Get-Acl -LiteralPath $destination
$acl.SetAccessRuleProtection($true,$false)
$rule=New-Object System.Security.AccessControl.FileSystemAccessRule($identity,'FullControl','Allow')
$acl.SetAccessRule($rule)
Set-Acl -LiteralPath $destination -AclObject $acl
Write-Host "$Provider credential saved for this Windows user only. No API request was made."
$key.Dispose()
