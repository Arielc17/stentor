"""Set up Stentor: a private Python environment, its packages and the Kokoro voice models.

Run it once with Python 3.10 or newer (the plugin's /stentor:install command prints the
exact line). Safe to run again: it repairs what is missing and skips what is in place.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import venv
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stentor  # noqa: E402

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
MODELS = {
    "kokoro-v1.0.int8.onnx": "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}
PACKAGES = ["kokoro-onnx>=0.4.7,<0.5"]
NO_VENV = ("Could not create the Python environment{detail}. On Debian or Ubuntu install "
           "python3-venv (sudo apt install python3-venv) and run this installer again.")
MAC_CERTIFICATES = (" On macOS with Python from python.org, run 'Install Certificates.command' "
                    "from its Applications folder first.")


def venv_python(folder: Path) -> Path:
    return folder / ("Scripts/python.exe" if stentor.WINDOWS else "bin/python3")


def pip_works(python: Path) -> bool:
    try:
        done = subprocess.run([str(python), "-m", "pip", "--version"], capture_output=True)
    except OSError:
        return False
    return done.returncode == 0


def ensure_venv(environment: Path) -> Path:
    """A venv with a working pip; one left half made by an earlier run is made again."""
    python = venv_python(environment)
    if python.exists() and pip_works(python):
        return python
    print("Creating the Python environment…", flush=True)
    try:
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
    except (Exception, SystemExit) as error:  # ensurepip is missing on some Linux distributions
        raise SystemExit(NO_VENV.format(detail=f" ({error})")) from error
    if not pip_works(python):
        raise SystemExit(NO_VENV.format(detail=""))
    return python


def needs_sounddevice() -> bool:
    """Linux plays through pw-play, paplay or aplay when one is there; the rest through PortAudio."""
    if not stentor.LINUX:
        return True
    return not any(shutil.which(name) for name, _ in stentor.CommandPlayer.COMMANDS)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(name: str, expected: str, folder: Path) -> None:
    target = folder / name
    if target.exists() and sha256(target) == expected:
        print(f"  {name}: already there")
        return
    partial = folder / f"{name}.part"
    print(f"  {name}: downloading…", flush=True)
    try:
        with urllib.request.urlopen(f"{RELEASE}/{name}", timeout=60) as response, partial.open("wb") as out:
            shutil.copyfileobj(response, out, 1 << 20)
    except (urllib.error.URLError, OSError) as error:
        partial.unlink(missing_ok=True)
        hint = MAC_CERTIFICATES if "CERTIFICATE_VERIFY_FAILED" in str(error) else ""
        raise SystemExit(f"Could not download {name} ({error}). Check the connection and run "
                         f"this installer again.{hint}") from error
    if sha256(partial) != expected:
        partial.unlink(missing_ok=True)
        raise SystemExit(f"{name} does not match its SHA-256; run this installer again.")
    partial.replace(target)


def main() -> None:
    if sys.version_info < (3, 10):
        raise SystemExit("Stentor needs Python 3.10 or newer.")
    data = stentor.data_dir()
    print(f"Data folder: {data}")
    python = ensure_venv(data / "venv")

    packages = PACKAGES + (["sounddevice>=0.4"] if needs_sounddevice() else [])
    print(f"Installing {', '.join(packages)}…", flush=True)
    try:
        subprocess.run([str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                        "--upgrade", *packages], check=True)
    except subprocess.CalledProcessError as error:
        raise SystemExit("pip could not install the packages; the messages above say why. "
                         "Run this installer again once that is fixed.") from error

    print("Kokoro voices (about 120 MB):", flush=True)
    models = data / "kokoro"
    models.mkdir(parents=True, exist_ok=True)
    for name, expected in MODELS.items():
        download(name, expected, models)

    print("\nDone. Restart Claude Code (or run /reload-plugins) and press 1 to listen.")


if __name__ == "__main__":
    main()
