param(
    [Parameter(Mandatory = $true)]
    [string]$SourcePath,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$wordApplication = $null
$wordDocument = $null
$exitCode = 0

try {
    $wordApplication = New-Object -ComObject Word.Application
    $wordApplication.Visible = $false
    $wordApplication.DisplayAlerts = 0
    $wordDocument = $wordApplication.Documents.Open($SourcePath, $false, $true)
    $wordDocument.SaveAs2($OutputPath, 17)
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    $exitCode = 1
}
finally {
    if ($null -ne $wordDocument) {
        $wordDocument.Close(0)
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($wordDocument)
    }
    if ($null -ne $wordApplication) {
        $wordApplication.Quit()
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($wordApplication)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

exit $exitCode
