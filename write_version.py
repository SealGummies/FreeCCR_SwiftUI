import subprocess

def get_git_version():
    try:
        return subprocess.check_output(["git", "describe", "--tags"], encoding="utf-8").strip()
    except Exception:
        return "v0.0.0-internal"

with open("src/version.py", "w") as f:
    f.write(f'VERSION = "{get_git_version()}"\n')