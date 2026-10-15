import subprocess; print(subprocess.run(['wsl.exe', '-e', 'bash', 'test_script.sh'], capture_output=True, text=True))
