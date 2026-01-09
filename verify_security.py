import os
import sys
import time
import requests
import subprocess
import signal
from termcolor import colored

# Configuration
PORT = 8001  # Use a different port to avoid conflicts
BASE_URL = f"http://127.0.0.1:{PORT}"

def run_server(env):
    """Run the FastAPI server in a subprocess with specific environment variables"""
    env_vars = os.environ.copy()
    env_vars.update(env)
    env_vars["PORT"] = str(PORT)
    env_vars["BOT_AUTO_START"] = "false"  # Don't start bot logic
    env_vars["SUPABASE_URL"] = "https://example.supabase.co" # Mock
    # Mock a valid-looking JWT for Supabase key (Header.Payload.Signature)
    env_vars["SUPABASE_SERVICE_ROLE_KEY"] = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UiLCJpYXQiOjE2MDAwMDAwMDAsImV4cCI6MjAwMDAwMDAwMH0.mocksignature" 
    env_vars["DERIV_API_TOKEN"] = "mock_token_for_test" # Mock to pass config validation
    env_vars["DERIV_APP_ID"] = "1089" # Mock
    
    print(f"DTO: Starting server with {env['ENVIRONMENT']} environment...")
    
    # Use config.PORT via env var, but uvicorn arg overrides it usually
    # We will pass port to uvicorn directly
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        env=env_vars,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    time.sleep(5) # Wait for startup
    
    # Check if process is still running
    if process.poll() is not None:
        stdout, stderr = process.communicate()
        print(colored(f"❌ Server failed to start with code {process.returncode}", "red"))
        print(f"STDOUT:\n{stdout}")
        print(f"STDERR:\n{stderr}")
        raise RuntimeError("Server failed to start")
        
    return process

def check_endpoint(path, expected_status, description):
    try:
        response = requests.get(f"{BASE_URL}{path}")
        if response.status_code == expected_status:
            print(colored(f"[PASS] {description}: Passed ({response.status_code})", "green"))
            return True
        else:
            print(colored(f"[FAIL] {description}: Failed (Expected {expected_status}, got {response.status_code})", "red"))
            return False
    except Exception as e:
        print(colored(f"[ERR] {description}: Error ({e})", "red"))
        # If connection failed, check if server is still alive and print logs
        return False

def verify_docs_disabled_in_prod():
    print(colored("\n--- Verifying Production Security ---", "cyan"))
    process = run_server({
        "ENVIRONMENT": "production",
        "DISABLE_DOCS_IN_PRODUCTION": "true",
        "ENABLE_AUTHENTICATION": "false" # Simplify
    })
    
    try:
        # Check Docs (Should be 404)
        check_endpoint("/docs", 404, "Docs disabled in production")
        check_endpoint("/redoc", 404, "Redoc disabled in production")
        check_endpoint("/openapi.json", 404, "OpenAPI disabled in production")
        
        # Check Health (Should be 200)
        check_endpoint("/health", 200, "Health check available")
        
        # Verify Health Content
        try:
            resp = requests.get(f"{BASE_URL}/health")
            if resp.json() == {"status": "ok"}:
                print(colored("[PASS] Health check content minimal: Passed", "green"))
            else:
                print(colored(f"[FAIL] Health check content leaking: {resp.json()}", "red"))
        except:
            print(colored("[FAIL] Failed to get health content", "red"))
            
    finally:
        process.terminate()
        stdout, stderr = process.communicate()
        print(colored("\n--- Server Output ---", "yellow"))
        print(f"STDOUT:\n{stdout}")
        print(f"STDERR:\n{stderr}")
        print(colored("---------------------", "yellow"))

def verify_docs_enabled_in_dev():
    print(colored("\n--- Verifying Development Mode ---", "cyan"))
    process = run_server({
        "ENVIRONMENT": "development",
        "DISABLE_DOCS_IN_PRODUCTION": "true" # Should be ignored in dev
    })
    
    try:
        # Check Docs (Should be 200)
        check_endpoint("/docs", 200, "Docs enabled in development")
        check_endpoint("/health", 200, "Health check available")
        
    finally:
        process.terminate()
        process.wait()

def verify_rate_limiting():
    print(colored("\n--- Verifying Rate Limiting ---", "cyan"))
    process = run_server({
        "ENVIRONMENT": "production",
        "RATE_LIMIT_ENABLED": "true"
    })
    
    try:
        print("Sending rapid requests to /health...")
        limit_hit = False
        for i in range(10):
            response = requests.get(f"{BASE_URL}/health")
            if response.status_code == 429:
                print(colored(f"✅ Rate limiting active: 429 received on request #{i+1}", "green"))
                limit_hit = True
                break
        
        if not limit_hit:
            print(colored("❌ Rate limiting failed: 429 never received", "red"))
            
    finally:
        process.terminate()
        process.wait()

if __name__ == "__main__":
    try:
        # Install termcolor if missing
        try:
            import termcolor
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "termcolor"])
            from termcolor import colored
            
        verify_docs_disabled_in_prod()
        verify_docs_enabled_in_dev()
        verify_rate_limiting()
        
    except KeyboardInterrupt:
        print("\nAborted.")
