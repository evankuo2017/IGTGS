#!/usr/bin/env python3
"""
Beat This! Web Application Setup Script
Automatically downloads, fixes, and installs Beat This! package
"""

import os
import sys
import subprocess
import tempfile
import shutil

def run_command(cmd, description):
    """Run a shell command and handle errors"""
    print(f"[*] {description}...")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] {description} failed:")
        print(result.stderr)
        return False
    return True

def install_beatthis():
    """Download, fix, and install Beat This! package"""
    
    # Create temporary directory
    temp_dir = tempfile.mkdtemp()
    print(f"[*] Using temporary directory: {temp_dir}")
    
    try:
        # Clone repository
        clone_cmd = f"git clone https://github.com/CPJKU/beat_this.git {temp_dir}/beat_this"
        if not run_command(clone_cmd, "Cloning Beat This! repository"):
            return False
        
        # Fix pyproject.toml
        pyproject_path = os.path.join(temp_dir, "beat_this", "pyproject.toml")
        print("[*] Fixing pyproject.toml configuration...")
        
        with open(pyproject_path, 'r') as f:
            lines = f.readlines()
        
        # Fix license field and remove license-files
        fixed_lines = []
        for line in lines:
            # Fix license format
            if 'license = "MIT"' in line:
                fixed_lines.append(line.replace('license = "MIT"', 'license = {text = "MIT"}'))
            # Remove license-files line
            elif 'license-files' in line:
                continue
            else:
                fixed_lines.append(line)
        
        with open(pyproject_path, 'w') as f:
            f.writelines(fixed_lines)
        
        print("[*] Configuration fixed")
        
        # Install package
        install_cmd = f"pip install -e {temp_dir}/beat_this"
        if not run_command(install_cmd, "Installing Beat This! package"):
            return False
        
        print("[SUCCESS] Beat This! installed successfully")
        return True
        
    finally:
        # Cleanup (optional, can keep for debugging)
        # shutil.rmtree(temp_dir)
        pass

def main():
    """Main installation process"""
    print("=" * 70)
    print("Beat This! Web Application - Setup Script")
    print("=" * 70)
    print()
    
    # Check Python version
    if sys.version_info < (3, 8):
        print("[ERROR] Python 3.8 or higher required")
        return 1
    
    # Check if pip is available
    if not run_command("pip --version", "Checking pip"):
        print("[ERROR] pip not found")
        return 1
    
    # Check if git is available
    if not run_command("git --version", "Checking git"):
        print("[ERROR] git not found. Install with: conda install -c conda-forge git")
        return 1
    
    # Install Beat This!
    if not install_beatthis():
        print("\n[FAILED] Installation failed")
        return 1
    
    print()
    print("=" * 70)
    print("Installation Complete")
    print("=" * 70)
    print()
    print("[Next Steps]")
    print("  1. Start the application:")
    print("     python beatthis_app.py")
    print()
    print("  2. Open browser:")
    print("     http://localhost:5000")
    print()
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
