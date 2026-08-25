# Start the Order Service

# Set environment variables
$env:DATABASE_URL = "sqlite:///orders.db"
$env:API_KEY = "prod-key-secret"
$env:TAX_RATE = "0.10"
$env:PORT = "8000"
$env:NOTIFICATION_SERVICE_URL = "http://notification-svc:9000/api/notifications"
$env:PAYMENT_GATEWAY_URL = "http://payment-svc:9001/api/payments"

# Get the directory of the current script
$ScriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Definition

# Construct the path to the Python application
# Assuming the Python application is in a 'src' directory relative to the script
$AppPath = Join-Path -Path $ScriptDirectory -ChildPath "src\app.py"

Write-Host "Starting Order Service on port $env:PORT..."

# Execute the Python application using the python interpreter
# Ensure 'python' is in your system's PATH or provide the full path to the python executable
# Example: & "C:\Python39\python.exe" -m src.app
& python -m src.app

# Check the exit code of the Python process
if ($LASTEXITCODE -ne 0) {
    Write-Error "Python application exited with error code $LASTEXITCODE."
    exit $LASTEXITCODE
}

Write-Host "Order Service started successfully."