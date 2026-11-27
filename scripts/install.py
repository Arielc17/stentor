"""Set up Stentor: a private Python environment, its packages and the Kokoro voice models.

The plugin's /stentor:install command runs it with --progress, which writes one JSON
object per line on stdout for the progress line under the prompt; without it, it reports
in plain words for a terminal. Needs Python 3.10 or newer. Safe to run again: it repairs
what is missing and skips what is in place.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import venv
from pathlib import Path
from typing import Callable, TextIO

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


class Progress:
    """--progress: each step as one JSON line, a download every 5 % or more, always its end."""

    def __init__(self, out: TextIO) -> None:
        self.out = out
        self.shown: tuple[int, int] | None = None

    def step(self, step: str, **fields: int) -> None:
        print(json.dumps({"step": step, **fields}), file=self.out, flush=True)

    def voices(self, file: int, of: int, percent: int) -> None:
        last = self.shown
        if last is None or last[0] != file or percent >= last[1] + 5 or (percent == 100 and last[1] != 100):
            self.shown = (file, percent)
            self.step("voices", file=file, of=of, percent=percent)


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


def download(name: str, expected: str, folder: Path,
             on_percent: Callable[[int], None] | None = None) -> None:
    """Fetches one model file, checks its SHA-256 and says how far it got when the size is known."""
    target = folder / name
    if target.exists() and sha256(target) == expected:
        print(f"  {name}: already there")
        if on_percent:
            on_percent(100)
        return
    partial = folder / f"{name}.part"
    print(f"  {name}: downloading…", flush=True)
    try:
        with urllib.request.urlopen(f"{RELEASE}/{name}", timeout=60) as response, partial.open("wb") as out:
            total = int(getattr(response, "headers", {}).get("Content-Length") or 0)
            done = 0
            while block := response.read(1 << 20):
                out.write(block)
                done += len(block)
                if on_percent and total:
                    on_percent(min(100, done * 100 // total))
    except (urllib.error.URLError, OSError) as error:
        partial.unlink(missing_ok=True)
        hint = MAC_CERTIFICATES if "CERTIFICATE_VERIFY_FAILED" in str(error) else ""
        raise SystemExit(f"Could not download {name} ({error}). Check the connection and run "
                         f"this installer again.{hint}") from error
    if sha256(partial) != expected:
        partial.unlink(missing_ok=True)
        raise SystemExit(f"{name} does not match its SHA-256; run this installer again.")
    partial.replace(target)


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    progress = Progress(sys.stdout) if "--progress" in args else None
    # With --progress, stdout carries the JSON lines alone; the plain words go to stderr.
    with contextlib.redirect_stdout(sys.stderr) if progress else contextlib.nullcontext():
        install(progress)


def install(progress: Progress | None) -> None:
    if sys.version_info < (3, 10):
        raise SystemExit("Stentor needs Python 3.10 or newer.")
    data = stentor.data_dir()
    print(f"Data folder: {data}")
    if progress:
        progress.step("environment")
    python = ensure_venv(data / "venv")

    packages = PACKAGES + (["sounddevice>=0.4"] if needs_sounddevice() else [])
    print(f"Installing {', '.join(packages)}…", flush=True)
    if progress:
        progress.step("packages")
    try:
        subprocess.run([str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                        "--upgrade", *packages], check=True, stdout=sys.stderr if progress else None)
    except subprocess.CalledProcessError as error:
        raise SystemExit("pip could not install the packages; the messages above say why. "
                         "Run this installer again once that is fixed.") from error

    print("Kokoro voices (about 120 MB):", flush=True)
    models = data / "kokoro"
    models.mkdir(parents=True, exist_ok=True)
    for file, (name, expected) in enumerate(MODELS.items(), start=1):
        report = (lambda percent, file=file: progress.voices(file, len(MODELS), percent)) if progress else None
        download(name, expected, models, report)

    print("\nDone. Press 1 in Claude Code to listen.")
    if progress:
        progress.step("done")


if __name__ == "__main__":
    main()
