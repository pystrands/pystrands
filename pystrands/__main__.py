import sys
import os
import platform
import subprocess
import urllib.request
import tempfile
import shutil
from pathlib import Path

def get_binary_url():
    """Get the appropriate binary URL based on the system."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    
    # Map platform and architecture to binary URLs
    base_url = "https://github.com/pystrands/pystrands-go/releases/download/v0.1.0"
    
    if system == "linux":
        if machine in ["x86_64", "amd64"]:
            return f"{base_url}/pystrands-server-linux-amd64"
        elif machine in ["aarch64", "arm64"]:
            return f"{base_url}/pystrands-server-linux-arm64"
    elif system == "darwin":
        if machine in ["x86_64", "amd64"]:
            return f"{base_url}/pystrands-server-darwin-amd64"
        elif machine in ["aarch64", "arm64"]:
            return f"{base_url}/pystrands-server-darwin-arm64"
    elif system == "windows":
        if machine in ["x86_64", "amd64"]:
            return f"{base_url}/pystrands-server-windows-amd64.exe"
    
    raise RuntimeError(f"Unsupported platform: {system} {machine}")

def download_binary(url, dest_path):
    """Download the binary from the given URL."""
    print(f"Downloading server binary from {url}...")
    with urllib.request.urlopen(url) as response:
        with open(dest_path, 'wb') as f:
            shutil.copyfileobj(response, f)
    
    # Make binary executable on Unix-like systems
    if platform.system() != "windows":
        os.chmod(dest_path, 0o755)

def get_binary_path():
    """Get the path to the server binary, downloading if necessary."""
    # Create a directory in the user's home directory to store the binary
    home_dir = Path.home()
    pystrands_dir = home_dir / ".pystrands"
    pystrands_dir.mkdir(exist_ok=True)
    
    # Determine binary name based on platform
    binary_name = "pystrands-server"
    if platform.system() == "windows":
        binary_name += ".exe"
    
    binary_path = pystrands_dir / binary_name
    
    # Download binary if it doesn't exist
    if not binary_path.exists():
        url = get_binary_url()
        download_binary(url, binary_path)
    
    return binary_path

def main():
    if len(sys.argv) < 2 or sys.argv[1] != "server":
        print("Usage: python -m pystrands server [--ws-port <port>(optional)] [--tcp-port <port>(optional)]")
        sys.exit(1)
    
    try:
        # Get the path to the server binary
        binary_path = get_binary_path()
        
        # Pass all arguments after "server" to the binary
        server_args = sys.argv[2:]
        
        # Run the server binary with the provided arguments
        process = subprocess.Popen([str(binary_path)] + server_args)
        process.wait()
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 