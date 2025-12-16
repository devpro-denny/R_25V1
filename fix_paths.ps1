# PowerShell script to remove Windows paths from Python docstrings
# Save this as fix_paths.ps1 and run it

Write-Host "Fixing Python files with Windows path issues..." -ForegroundColor Yellow

# Get all Python files in the app directory
$pythonFiles = Get-ChildItem -Path "app" -Filter "*.py" -Recurse

foreach ($file in $pythonFiles) {
    Write-Host "Checking: $($file.FullName)" -ForegroundColor Cyan
    
    # Read the file content
    $content = Get-Content $file.FullName -Raw
    
    # Check if it contains the problematic path pattern
    if ($content -match "C:\\Users\\owner\\ALX\\R50BOT") {
        Write-Host "  -> Found path issue, fixing..." -ForegroundColor Red
        
        # Backup the file
        Copy-Item $file.FullName "$($file.FullName).backup"
        
        # Remove lines containing the Windows path
        $lines = Get-Content $file.FullName
        $fixedLines = $lines | Where-Object { $_ -notmatch "C:\\Users\\owner\\ALX\\R50BOT" }
        
        # Write back the fixed content
        $fixedLines | Set-Content $file.FullName -Encoding UTF8
        
        Write-Host "  -> Fixed! Backup saved as $($file.Name).backup" -ForegroundColor Green
    }
}

Write-Host "`nDone! All files have been checked and fixed." -ForegroundColor Green
Write-Host "Backups have been created with .backup extension" -ForegroundColor Yellow
