[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,
    [string]$SecretName = "newsagent-smtp-password"
)

$ErrorActionPreference = "Stop"
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    throw "Google Cloud CLI is not installed."
}

& gcloud config set project $ProjectId | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Unable to select project $ProjectId" }
& gcloud secrets describe $SecretName *> $null
if ($LASTEXITCODE -ne 0) {
    & gcloud secrets create $SecretName --replication-policy=automatic
    if ($LASTEXITCODE -ne 0) { throw "Unable to create secret $SecretName" }
}

$secureValue = Read-Host "SMTP app password" -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
$tempPath = [System.IO.Path]::GetTempFileName()
try {
    $plainValue = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    [System.IO.File]::WriteAllText($tempPath, $plainValue, [Text.UTF8Encoding]::new($false))
    & gcloud secrets versions add $SecretName "--data-file=$tempPath"
    if ($LASTEXITCODE -ne 0) { throw "Unable to add the secret version" }
}
finally {
    if ($pointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
    $plainValue = $null
    Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
}

Write-Host "Added a new enabled version to $SecretName."
