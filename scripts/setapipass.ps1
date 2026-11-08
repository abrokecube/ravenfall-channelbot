<#
Prompts for the Ravenfall API username and password and sets RAVENFALL_API_USER
and RAVENFALL_API_PASS for the current PowerShell session only. Nothing is
written to disk.

Dot-source it so it runs in your session (a normal run would set the variables
in a child process that exits immediately):

    . .\scripts\setapipass.ps1

The password is read as a SecureString, so it isn't echoed, kept in shell
history or passed as a process argument. The variables disappear when the
session closes. Programs started from this session (the bot, scripts) inherit
them. python-dotenv doesn't override existing variables, so they win over any
.env values.
#>
$__user = Read-Host 'Ravenfall API username'
$__secure = Read-Host 'Ravenfall API password' -AsSecureString
$__bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($__secure)
try { $__plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($__bstr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($__bstr) }

if ([string]::IsNullOrWhiteSpace($__user) -or [string]::IsNullOrEmpty($__plain)) {
    Write-Host 'Username or password empty, nothing set.'
} else {
    $env:RAVENFALL_API_USER = $__user.Trim()
    $env:RAVENFALL_API_PASS = $__plain
    Write-Host 'RAVENFALL_API_USER and RAVENFALL_API_PASS set for this session.'
}
Remove-Variable __user, __secure, __bstr, __plain -ErrorAction SilentlyContinue
