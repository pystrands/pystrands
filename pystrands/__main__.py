import sys
import os
import platform
import subprocess
import urllib.request
import shutil
from pathlib import Path

# Must match the pystrands-go release tag
BROKER_VERSION = "v1.0.0"


def get_binary_url():
    """Get the appropriate binary URL based on the system."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    base_url = f"https://github.com/pystrands/pystrands-go/releases/download/{BROKER_VERSION}"

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

    if platform.system() != "windows":
        os.chmod(dest_path, 0o755)


def get_binary_path():
    """Get the path to the server binary, downloading if necessary."""
    pystrands_dir = Path.home() / ".pystrands"
    pystrands_dir.mkdir(exist_ok=True)

    binary_name = "pystrands-server"
    if platform.system() == "windows":
        binary_name += ".exe"

    binary_path = pystrands_dir / binary_name
    version_file = pystrands_dir / ".broker_version"

    # Re-download if binary missing or version changed
    current_version = version_file.read_text().strip() if version_file.exists() else None
    if not binary_path.exists() or current_version != BROKER_VERSION:
        url = get_binary_url()
        download_binary(url, binary_path)
        version_file.write_text(BROKER_VERSION)

    return binary_path


def main():
    if len(sys.argv) < 2 or sys.argv[1] != "server":
        print("Usage: python -m pystrands server [--ws-port <port>] [--tcp-port <port>] [--queue-size <n>]")
        sys.exit(1)

    try:
        binary_path = get_binary_path()
        server_args = sys.argv[2:]
        process = subprocess.Popen([str(binary_path)] + server_args)
        process.wait()
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
