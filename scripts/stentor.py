"""Stentor: read Claude Code answers aloud with a local Kokoro voice.

Usage:
  stentor.py save <session>    store stdin as the answer to read for a session
  stentor.py speak <session>   pause other players, read that answer, then resume them
  stentor.py stop              stop the reading and resume the paused players
  stentor.py switch            switch to the next configured voice, print its name
  stentor.py status            print "speaking" or "idle"

Environment:
  STENTOR_VOICES   comma-separated Kokoro voices to cycle through (default em_alex,ef_dora)
  STENTOR_SPEED    speech speed, 0.5 to 2.0 (default 1.0)
  STENTOR_DATA     data folder (default: see data_dir)
  STENTOR_STATE    state folder for the current reading (default: see state_dir)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_VOICES = ("em_alex", "ef_dora")
# Kokoro voice names start with a language letter and a gender letter: "ef_dora".
LANGUAGES = {
    "a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi",
    "i": "it", "j": "ja", "p": "pt-br", "z": "cmn",
}
LINK_WORD = {"es": "un enlace", "fr-fr": "un lien", "it": "un link", "pt-br": "um link"}
GENDER = {
    "es": {"f": "voz de mujer", "m": "voz de hombre"},
    "en": {"f": "female voice", "m": "male voice"},
}
MESSAGES = {
    "es": {
        "not_installed": "Faltan las voces de Stentor: escribe /stentor:install",
        "unknown_voice": "La voz {voice} no existe; revisa Voices en /plugin",
        "not_private": "{folder} no es una carpeta privada tuya; bórrala o define STENTOR_STATE",
    },
    "en": {
        "not_installed": "Stentor voices are not installed: type /stentor:install",
        "unknown_voice": "There is no voice named {voice}; check Voices in /plugin",
        "not_private": "{folder} is not a private folder of yours; remove it or set STENTOR_STATE",
    },
}
ANSWERS_KEPT_FOR = 7 * 24 * 3600
MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_PATH = "/org/mpris/MediaPlayer2"
MPRIS_PLAYER = "org.mpris.MediaPlayer2.Player"
WINDOWS = sys.platform == "win32"
LINUX = sys.platform.startswith("linux")


def data_dir() -> Path:
    """Where the voice models and the venv live: kept across plugin updates."""
    custom = os.environ.get("STENTOR_DATA")
    if custom:
        return Path(custom)
    if WINDOWS and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "stentor"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "stentor"


def state_dir() -> Path:
    """Where the current reading is tracked: per user, gone after a reboot."""
    custom = os.environ.get("STENTOR_STATE")
    if custom:
        return Path(custom)
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "stentor"
    user = re.sub(r"[^A-Za-z0-9_-]", "", os.environ.get("USER") or os.environ.get("USERNAME") or "")
    return Path(tempfile.gettempdir()) / f"stentor-{user or 'user'}"


def ensure_state_dir() -> Path:
    """Create the state folder private to this user; refuse one another user could control."""
    folder = state_dir()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not WINDOWS:
        info = os.lstat(folder)
        if stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid():
            raise SystemExit(message("not_private", folder=folder))
        if info.st_mode & 0o077:
            os.chmod(folder, 0o700)
    return folder


def model_files() -> tuple[Path, Path]:
    folder = data_dir() / "kokoro"
    return folder / "kokoro-v1.0.int8.onnx", folder / "voices-v1.0.bin"


def configured_voices() -> list[str]:
    raw = os.environ.get("STENTOR_VOICES", "")
    # Names are kept as written: a mistyped one is reported when it is read, not replaced.
    voices = [v.strip() for v in raw.split(",") if v.strip()]
    return voices or list(DEFAULT_VOICES)


def speed() -> float:
    try:
        value = float(os.environ.get("STENTOR_SPEED", "1.0"))
    except ValueError:
        return 1.0
    return min(max(value, 0.5), 2.0)


def language(voice: str) -> str:
    return LANGUAGES.get(voice[:1], "en-us")


def message(key: str, **values: object) -> str:
    """A message in the language of the voice that reads, Spanish or English."""
    words = MESSAGES["es" if language(configured_voices()[0]) == "es" else "en"]
    return words[key].format(**values)


def installed_voices() -> set[str] | None:
    """The voice names in the voices archive, or None while the models are missing."""
    model, voices = model_files()
    if not model.exists() or not voices.exists():
        return None
    try:
        with zipfile.ZipFile(voices) as archive:
            return {name.removesuffix(".npy") for name in archive.namelist()}
    except (OSError, zipfile.BadZipFile):
        return None


def describe(voice: str) -> str:
    words = GENDER["es" if language(voice) == "es" else "en"]
    kind = words.get(voice[1:2])
    return f"{voice} ({kind})" if kind else voice


def chosen_voice_file() -> Path:
    return data_dir() / "chosen-voice"


def current_voice() -> str:
    voices = configured_voices()
    try:
        voice = chosen_voice_file().read_text().strip()
    except OSError:
        return voices[0]
    return voice if voice in voices else voices[0]


def switch_voice() -> str:
    voices = configured_voices()
    current = current_voice()
    following = voices[(voices.index(current) + 1) % len(voices)]
    chosen_voice_file().parent.mkdir(parents=True, exist_ok=True)
    chosen_voice_file().write_text(following)
    return describe(following)


def safe_session(session: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", session) or "no-session"


def answer_file(session: str) -> Path:
    return state_dir() / f"{safe_session(session)}.txt"


def save_answer(session: str, text: str) -> None:
    """Store the answer to read, and drop answers nobody asked for in a week."""
    target = answer_file(session)
    cutoff = time.time() - ANSWERS_KEPT_FOR
    for old in state_dir().glob("*.txt"):
        try:
            if old != target and old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass
    target.write_text(text, encoding="utf-8")


def speakable(text: str, lang: str = "es") -> str:
    """Drop what sounds bad aloud: code blocks, tables, markup, raw URLs, paths."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            continue
        stripped = re.sub(r"^#{1,6}\s*", "", stripped)
        stripped = re.sub(r"^([-*+]|\d+\.)\s+", "", stripped)
        stripped = re.sub(r"^>\s*", "", stripped)
        lines.append(stripped)
    text = "\n".join(lines)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", LINK_WORD.get(lang, "a link"), text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    # A path keeps its file name; every folder in it has a letter, so dates stay.
    text = re.sub(r"(?:\b[A-Za-z]:)?~?[/\\]?(?:[\w.-]*[A-Za-z_][\w.-]*[/\\]){2,}([\w.-]+)", r"\1", text)
    text = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", text)
    text = re.sub(r"[*#>|]", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def sentences(text: str) -> list[str]:
    """Short pieces, so the first one is ready in a moment."""
    pieces = re.split(r"(?<=[.!?:;])\s+|\n+", text)
    return [piece.strip() for piece in pieces if re.search(r"\w", piece)]


# --- The reader process: started detached, tracked by a lock it holds. ------
#
# The reader holds reader.lock for as long as it lives, and the system lets go of
# it when the reader dies, however it dies. Only while that lock is held does
# reader.pid name the reader, so a pid left behind never reaches another process.
# speak and stop take control.lock, so two presses never start two readers.

def pid_file() -> Path:
    return state_dir() / "reader.pid"


def reader_lock_file() -> Path:
    return state_dir() / "reader.lock"


def control_lock_file() -> Path:
    return state_dir() / "control.lock"


def log_file() -> Path:
    return state_dir() / "reader.log"


def lock(handle, wait: bool) -> bool:
    """Lock an open file; without wait, False when someone else holds it."""
    try:
        if WINDOWS:
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if wait else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
    except OSError:
        if wait:
            raise
        return False
    return True


@contextmanager
def locked(path: Path, wait: bool) -> Iterator[bool]:
    """Hold the lock at path for the block; yields whether it was acquired."""
    with path.open("a+") as handle:
        acquired = lock(handle, wait)
        try:
            yield acquired
        finally:
            if acquired and WINDOWS:
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def reader_alive() -> int | None:
    """The reader's pid while one is reading, else None."""
    with locked(reader_lock_file(), wait=False) as free:
        if free:
            return None
    try:
        pid = int(pid_file().read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 1 else None


def kill_reader() -> None:
    pid = reader_alive()
    if pid is not None:
        if WINDOWS:
            quiet("taskkill", "/PID", str(pid), "/T", "/F")
        else:
            try:
                os.killpg(pid, signal.SIGTERM)
            except OSError:
                pass
    pid_file().unlink(missing_ok=True)


def start_reader(source: Path) -> None:
    """Start the reader detached, and return once it holds its lock or has failed."""
    options: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL}
    if WINDOWS:
        options["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
                                    | subprocess.CREATE_NO_WINDOW)  # type: ignore[attr-defined]
    else:
        options["start_new_session"] = True
    with log_file().open("w", encoding="utf-8") as log:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_read", str(source)],
                                 stderr=log, **options)
    deadline = time.monotonic() + 5
    while reader_alive() is None and child.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)


def run_reader(source: Path) -> None:
    """The reader itself: hold the lock, read, then resume the players if still current."""
    me = str(os.getpid())
    with locked(reader_lock_file(), wait=True):
        pid_file().write_text(me)
        try:
            play(source)
        finally:
            # A reader killed by stop or by a newer speak never gets here, so the
            # players stay paused for the next reader; one that ends does this.
            with locked(control_lock_file(), wait=True):
                try:
                    current = pid_file().read_text().strip()
                except OSError:
                    current = ""
                if current == me:
                    pid_file().unlink(missing_ok=True)
                    resume_others()


# --- Other players: paused while reading, on Linux through MPRIS. -----------

def quiet(*args: str) -> str:
    """Run a helper and return its stdout; audio control must never break reading."""
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout if done.returncode == 0 else ""


def paused_file() -> Path:
    return state_dir() / "paused.json"


def mpris_players() -> list[str]:
    if not LINUX or shutil.which("busctl") is None:
        return []
    out = quiet("busctl", "--user", "--json=short", "call", "org.freedesktop.DBus",
                "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames")
    try:
        names = json.loads(out)["data"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        return []
    # playerctld only proxies the real players; pausing it too would toggle them twice.
    return [name for name in names
            if name.startswith(MPRIS_PREFIX) and not name.endswith(".playerctld")]


def is_playing(player: str) -> bool:
    out = quiet("busctl", "--user", "--json=short", "get-property", player,
                MPRIS_PATH, MPRIS_PLAYER, "PlaybackStatus")
    try:
        return json.loads(out)["data"] == "Playing"
    except (ValueError, KeyError, TypeError):
        return False


def pause_others() -> None:
    """Pause the players that are playing and remember them; nothing the person paused."""
    if paused_file().exists():
        return
    players = [player for player in mpris_players() if is_playing(player)]
    for player in players:
        quiet("busctl", "--user", "call", player, MPRIS_PATH, MPRIS_PLAYER, "Pause")
    paused_file().write_text(json.dumps({"players": players}))


def resume_others() -> None:
    try:
        paused = json.loads(paused_file().read_text())
    except (OSError, ValueError):
        return
    paused_file().unlink(missing_ok=True)
    for player in paused.get("players", []):
        quiet("busctl", "--user", "call", player, MPRIS_PATH, MPRIS_PLAYER, "Play")


# --- Playback ---------------------------------------------------------------

class CommandPlayer:
    """A Linux player that reads raw 32-bit float mono samples on stdin."""

    COMMANDS = (
        ("pw-play", lambda rate: ["pw-play", "--raw", "--rate", str(rate), "--channels", "1",
                                  "--format", "f32", "-"]),
        ("paplay", lambda rate: ["paplay", "--raw", f"--rate={rate}", "--channels=1",
                                 "--format=float32le"]),
        ("aplay", lambda rate: ["aplay", "-q", "-t", "raw", "-f", "FLOAT_LE", "-r", str(rate),
                                "-c", "1"]),
    )

    def __init__(self, rate: int) -> None:
        argv = next(build(rate) for name, build in self.COMMANDS if shutil.which(name))
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def write(self, samples) -> None:
        self.process.stdin.write(samples.astype("float32").tobytes())
        self.process.stdin.flush()

    def close(self) -> None:
        self.process.stdin.close()
        self.process.wait()


class DevicePlayer:
    """The sound card through PortAudio (sounddevice): macOS, Windows, Linux without a CLI player."""

    def __init__(self, rate: int) -> None:
        import sounddevice

        self.stream = sounddevice.OutputStream(samplerate=rate, channels=1, dtype="float32")
        self.stream.start()

    def write(self, samples) -> None:
        self.stream.write(samples.astype("float32").reshape(-1, 1))

    def close(self) -> None:
        self.stream.stop()
        self.stream.close()


def open_player(rate: int):
    if LINUX and any(shutil.which(name) for name, _ in CommandPlayer.COMMANDS):
        return CommandPlayer(rate)
    return DevicePlayer(rate)


def play(source: Path) -> None:
    """Generate the next sentence while the current one plays."""
    import queue
    import threading

    from kokoro_onnx import Kokoro

    model, voices = model_files()
    kokoro = Kokoro(str(model), str(voices))
    voice = current_voice()
    lang = language(voice)
    ready: queue.Queue = queue.Queue(maxsize=3)

    failures: list[BaseException] = []

    def produce() -> None:
        try:
            for piece in sentences(speakable(source.read_text(encoding="utf-8"), lang)):
                ready.put(kokoro.create(piece, voice=voice, speed=speed(), lang=lang))
        except BaseException as error:  # handed to the main thread, which logs it
            failures.append(error)
        finally:
            ready.put(None)

    threading.Thread(target=produce, daemon=True).start()
    player = None
    while (item := ready.get()) is not None:
        samples, rate = item
        if player is None:
            player = open_player(rate)
        player.write(samples)
    if player is not None:
        player.close()
    if failures:
        raise failures[0]


def speak(session: str) -> None:
    """Start a detached reader so the caller returns at once."""
    with locked(control_lock_file(), wait=True):
        kill_reader()
        source = answer_file(session)
        if not source.exists() or not speakable(source.read_text(encoding="utf-8")):
            resume_others()
            return
        voices = installed_voices()
        if voices is None:
            resume_others()
            raise SystemExit(message("not_installed"))
        if current_voice() not in voices:
            resume_others()
            raise SystemExit(message("unknown_voice", voice=current_voice()))
        pause_others()
        start_reader(source)


def stop() -> None:
    with locked(control_lock_file(), wait=True):
        kill_reader()
        resume_others()


def main() -> None:
    ensure_state_dir()
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    argument = sys.argv[2] if len(sys.argv) > 2 else ""
    if command == "save":
        save_answer(argument, sys.stdin.buffer.read().decode("utf-8", errors="replace"))
    elif command == "speak":
        speak(argument)
    elif command == "_read":
        run_reader(Path(argument))
    elif command == "stop":
        stop()
    elif command == "switch":
        print(switch_voice())
    elif command == "status":
        print("speaking" if reader_alive() else "idle")
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
