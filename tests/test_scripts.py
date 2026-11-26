"""Tests for scripts/stentor.py and scripts/install.py; no audio is played and nothing is downloaded.

Run: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import install  # noqa: E402
import stentor  # noqa: E402


class Isolated(unittest.TestCase):
    """Every test gets its own data and state folders, never the person's own."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="stentor-test-"))
        self.env = mock.patch.dict(os.environ, {
            "STENTOR_DATA": str(self.folder / "datos"),
            "STENTOR_STATE": str(self.folder / "state"),
        })
        self.env.start()
        # The installer reports progress on stdout; keep the test output clean.
        self.output = mock.patch("sys.stdout", new=io.StringIO())
        self.output.start()

    def tearDown(self) -> None:
        self.output.stop()
        self.env.stop()
        import shutil

        shutil.rmtree(self.folder, ignore_errors=True)


class Folders(unittest.TestCase):
    def test_linux_and_macos_use_xdg_data_home_or_local_share(self) -> None:
        with mock.patch.object(stentor, "WINDOWS", False), \
                mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/datos"}, clear=True):
            self.assertEqual(stentor.data_dir(), Path("/datos/stentor"))
        with mock.patch.object(stentor, "WINDOWS", False), mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(Path, "home", return_value=Path("/home/ana")):
            self.assertEqual(stentor.data_dir(), Path("/home/ana/.local/share/stentor"))

    def test_windows_uses_localappdata(self) -> None:
        with mock.patch.object(stentor, "WINDOWS", True), \
                mock.patch.dict(os.environ, {"LOCALAPPDATA": "/appdata"}, clear=True):
            self.assertEqual(stentor.data_dir(), Path("/appdata/stentor"))

    def test_state_folder_is_per_user(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1000"}, clear=True):
            self.assertEqual(stentor.state_dir(), Path("/run/user/1000/stentor"))
        with mock.patch.dict(os.environ, {"USER": "ana"}, clear=True), \
                mock.patch.object(stentor.tempfile, "gettempdir", return_value="/tmp"):
            self.assertEqual(stentor.state_dir(), Path("/tmp/stentor-ana"))


class Voices(Isolated):
    def test_default_voices_and_cycling(self) -> None:
        os.environ.pop("STENTOR_VOICES", None)
        self.assertEqual(stentor.current_voice(), "em_alex")
        self.assertEqual(stentor.switch_voice(), "ef_dora (voz de mujer)")
        self.assertEqual(stentor.current_voice(), "ef_dora")
        self.assertEqual(stentor.switch_voice(), "em_alex (voz de hombre)")

    def test_configured_voices_keep_every_name_and_skip_blanks(self) -> None:
        os.environ["STENTOR_VOICES"] = "af_heart, ef-dora ,,am_michael"
        self.assertEqual(stentor.configured_voices(), ["af_heart", "ef-dora", "am_michael"])
        self.assertEqual(stentor.switch_voice(), "ef-dora (voz de mujer)")

    def test_only_blank_voices_mean_the_default_ones(self) -> None:
        os.environ["STENTOR_VOICES"] = " , "
        self.assertEqual(stentor.configured_voices(), ["em_alex", "ef_dora"])

    def test_a_chosen_voice_no_longer_configured_falls_back_to_the_first(self) -> None:
        os.environ["STENTOR_VOICES"] = "ef_dora"
        stentor.chosen_voice_file().parent.mkdir(parents=True)
        stentor.chosen_voice_file().write_text("em_alex")
        self.assertEqual(stentor.current_voice(), "ef_dora")

    def test_language_comes_from_the_voice(self) -> None:
        self.assertEqual(stentor.language("ef_dora"), "es")
        self.assertEqual(stentor.language("bf_emma"), "en-gb")
        self.assertEqual(stentor.language("qq_unknown"), "en-us")

    def test_speed_is_clamped(self) -> None:
        for raw, expected in (("1.3", 1.3), ("9", 2.0), ("0.1", 0.5), ("rápido", 1.0)):
            os.environ["STENTOR_SPEED"] = raw
            self.assertEqual(stentor.speed(), expected)


class Speakable(unittest.TestCase):
    def test_drops_code_tables_links_and_paths(self) -> None:
        text = ("# Título\n\n```bash\nrm -rf x\n```\n| a | b |\n- **Mira** [esto](https://x.y) en "
                "`~/proyectos/app/main.py` y https://ejemplo.com")
        self.assertEqual(stentor.speakable(text), "Título\n\nMira esto en main.py y un enlace")

    def test_links_are_named_in_the_voice_language(self) -> None:
        self.assertEqual(stentor.speakable("see https://x.y", "en-us"), "see a link")

    def test_windows_paths_keep_only_the_file_name(self) -> None:
        self.assertEqual(stentor.speakable("abre C:\\Users\\ana\\notas.txt"), "abre notas.txt")


class Sessions(Isolated):
    def test_session_names_cannot_escape_the_state_folder(self) -> None:
        self.assertEqual(stentor.answer_file("../../etc/passwd").name, "etcpasswd.txt")
        self.assertEqual(stentor.answer_file("abc-elegido").name, "abc-elegido.txt")

    def test_save_stores_utf8_from_stdin(self) -> None:
        subprocess.run([sys.executable, str(SCRIPTS / "stentor.py"), "save", "s1"],
                       input="¿Qué tal? ñandú".encode(), check=True, env=os.environ.copy())
        self.assertEqual(stentor.answer_file("s1").read_text(encoding="utf-8"), "¿Qué tal? ñandú")

    def test_speak_without_an_answer_starts_no_reader(self) -> None:
        subprocess.run([sys.executable, str(SCRIPTS / "stentor.py"), "speak", "nada"], check=True,
                       env=os.environ.copy())
        self.assertFalse(stentor.pid_file().exists())

    def test_status_and_stop_with_nothing_playing(self) -> None:
        run = [sys.executable, str(SCRIPTS / "stentor.py")]
        out = subprocess.run([*run, "status"], capture_output=True, text=True, check=True, env=os.environ.copy())
        self.assertEqual(out.stdout.strip(), "idle")
        subprocess.run([*run, "stop"], check=True, env=os.environ.copy())

    def test_unknown_command_prints_usage(self) -> None:
        out = subprocess.run([sys.executable, str(SCRIPTS / "stentor.py"), "otra"], capture_output=True,
                             text=True, env=os.environ.copy())
        self.assertEqual(out.returncode, 2)
        self.assertIn("Usage:", out.stdout)


class OtherPlayers(Isolated):
    def test_off_linux_nothing_is_paused_or_resumed(self) -> None:
        stentor.state_dir().mkdir(parents=True)
        with mock.patch.object(stentor, "LINUX", False), mock.patch.object(stentor, "quiet") as helper:
            stentor.pause_others()
            stentor.resume_others()
        helper.assert_not_called()
        self.assertFalse(stentor.paused_file().exists())

    def test_only_players_that_were_playing_are_resumed(self) -> None:
        stentor.state_dir().mkdir(parents=True)
        with mock.patch.object(stentor, "mpris_players", return_value=["org.mpris.MediaPlayer2.a",
                                                                   "org.mpris.MediaPlayer2.b"]), \
                mock.patch.object(stentor, "is_playing", side_effect=lambda p: p.endswith(".a")), \
                mock.patch.object(stentor, "quiet") as helper:
            stentor.pause_others()
            stentor.resume_others()
        verbs = [call.args[-1] for call in helper.call_args_list]
        targets = {call.args[3] for call in helper.call_args_list}
        self.assertEqual(verbs, ["Pause", "Play"])
        self.assertEqual(targets, {"org.mpris.MediaPlayer2.a"})


class Installer(Isolated):
    def test_venv_python_path_per_system(self) -> None:
        with mock.patch.object(install.stentor, "WINDOWS", True):
            self.assertEqual(install.venv_python(Path("v")), Path("v/Scripts/python.exe"))
        with mock.patch.object(install.stentor, "WINDOWS", False):
            self.assertEqual(install.venv_python(Path("v")), Path("v/bin/python3"))

    def test_sounddevice_only_where_no_cli_player_plays(self) -> None:
        with mock.patch.object(install.stentor, "LINUX", False):
            self.assertTrue(install.needs_sounddevice())
        with mock.patch.object(install.stentor, "LINUX", True), \
                mock.patch.object(install.shutil, "which", return_value=None):
            self.assertTrue(install.needs_sounddevice())
        with mock.patch.object(install.stentor, "LINUX", True), \
                mock.patch.object(install.shutil, "which", return_value="/usr/bin/pw-play"):
            self.assertFalse(install.needs_sounddevice())

    def test_a_model_already_in_place_is_not_downloaded_again(self) -> None:
        self.folder.joinpath("m.bin").write_bytes(b"modelo")
        expected = hashlib.sha256(b"modelo").hexdigest()
        with mock.patch.object(install.urllib.request, "urlopen") as fetch:
            install.download("m.bin", expected, self.folder)
        fetch.assert_not_called()

    def test_a_download_with_the_wrong_hash_is_rejected_and_removed(self) -> None:
        import io

        with mock.patch.object(install.urllib.request, "urlopen",
                               return_value=io.BytesIO(b"otra cosa")), \
                self.assertRaises(SystemExit):
            install.download("m.bin", hashlib.sha256(b"modelo").hexdigest(), self.folder)
        self.assertFalse(self.folder.joinpath("m.bin").exists())
        self.assertFalse(self.folder.joinpath("m.bin.part").exists())

    def test_a_good_download_lands_in_place(self) -> None:
        import io

        with mock.patch.object(install.urllib.request, "urlopen", return_value=io.BytesIO(b"modelo")):
            install.download("m.bin", hashlib.sha256(b"modelo").hexdigest(), self.folder)
        self.assertEqual(self.folder.joinpath("m.bin").read_bytes(), b"modelo")



def fake_models(folder: Path, voices: tuple[str, ...] = ("ef_dora", "em_alex")) -> None:
    """Model files in place: an empty model and a voices archive holding these names."""
    import zipfile

    models = folder / "datos" / "kokoro"
    models.mkdir(parents=True)
    (models / "kokoro-v1.0.int8.onnx").write_bytes(b"")
    with zipfile.ZipFile(models / "voices-v1.0.bin", "w") as archive:
        for voice in voices:
            archive.writestr(f"{voice}.npy", b"")


@unittest.skipIf(stentor.WINDOWS, "POSIX process groups")
class StaleReader(Isolated):
    def test_a_leftover_pid_of_another_process_is_never_signalled(self) -> None:
        other = subprocess.Popen(["sleep", "30"], start_new_session=True)
        self.addCleanup(other.kill)
        stentor.ensure_state_dir()
        stentor.pid_file().write_text(str(other.pid))

        self.assertIsNone(stentor.reader_alive())
        stentor.stop()

        self.assertIsNone(other.poll())


class ReaderLifecycle(Isolated):
    def setUp(self) -> None:
        super().setUp()
        stentor.ensure_state_dir()
        self.source = stentor.answer_file("s1")
        self.source.write_text("Hola.", encoding="utf-8")

    def test_a_reader_that_ends_resumes_the_players_and_clears_its_pid(self) -> None:
        seen = []
        with mock.patch.object(stentor, "play", side_effect=lambda _: seen.append(stentor.reader_alive())), \
                mock.patch.object(stentor, "resume_others") as resume:
            stentor.run_reader(self.source)

        self.assertEqual(seen, [os.getpid()])
        resume.assert_called_once()
        self.assertFalse(stentor.pid_file().exists())
        self.assertIsNone(stentor.reader_alive())

    def test_a_reader_replaced_by_a_newer_one_leaves_its_state_alone(self) -> None:
        def newer_reader_starts(_: Path) -> None:
            stentor.pid_file().write_text("424242")

        with mock.patch.object(stentor, "play", side_effect=newer_reader_starts), \
                mock.patch.object(stentor, "resume_others") as resume:
            stentor.run_reader(self.source)

        resume.assert_not_called()
        self.assertEqual(stentor.pid_file().read_text(), "424242")

    def test_speak_and_stop_hold_the_control_lock(self) -> None:
        fake_models(self.folder)
        held = []

        def check(*_: object) -> None:
            with stentor.locked(stentor.control_lock_file(), wait=False) as acquired:
                held.append(not acquired)

        with mock.patch.object(stentor, "start_reader", side_effect=check), \
                mock.patch.object(stentor, "pause_others"):
            stentor.speak("s1")
        with mock.patch.object(stentor, "kill_reader", side_effect=check), \
                mock.patch.object(stentor, "resume_others"):
            stentor.stop()

        self.assertEqual(held, [True, True])


class ReaderFailures(Isolated):
    def test_a_voice_the_model_rejects_ends_the_reading_instead_of_hanging(self) -> None:
        import threading
        import types

        class Kokoro:
            def __init__(self, *_: object) -> None:
                pass

            def create(self, *_: object, **__: object):
                raise KeyError("ef_dorra")

        fake = types.ModuleType("kokoro_onnx")
        fake.Kokoro = Kokoro  # type: ignore[attr-defined]
        source = self.folder / "answer.txt"
        source.write_text("Hola. Adiós.", encoding="utf-8")
        errors = []

        def run() -> None:
            try:
                stentor.play(source)
            except KeyError as error:
                errors.append(error)

        with mock.patch.dict(sys.modules, {"kokoro_onnx": fake}), \
                mock.patch.object(stentor, "open_player"):
            reader = threading.Thread(target=run, daemon=True)
            reader.start()
            reader.join(timeout=5)

        self.assertFalse(reader.is_alive())
        self.assertEqual(len(errors), 1)

    def run_speak(self, voices: str) -> subprocess.CompletedProcess:
        stentor.ensure_state_dir()
        stentor.answer_file("s1").write_text("Hola.", encoding="utf-8")
        env = {**os.environ, "STENTOR_VOICES": voices}
        return subprocess.run([sys.executable, str(SCRIPTS / "stentor.py"), "speak", "s1"],
                              capture_output=True, text=True, env=env)

    def test_speak_before_the_voices_are_installed_says_how_to_install_them(self) -> None:
        done = self.run_speak("em_alex")

        self.assertEqual(done.returncode, 1)
        self.assertIn("/stentor:install", done.stderr)
        self.assertFalse(stentor.pid_file().exists())

    def test_speak_with_an_unknown_voice_names_it_and_starts_nothing(self) -> None:
        fake_models(self.folder)
        done = self.run_speak("ef_dorra")

        self.assertEqual(done.returncode, 1)
        self.assertIn("ef_dorra", done.stderr)
        self.assertFalse(stentor.pid_file().exists())

    def test_speak_with_a_mistyped_voice_names_it_instead_of_reading_with_another(self) -> None:
        fake_models(self.folder)
        done = self.run_speak("ef-dora")

        self.assertEqual(done.returncode, 1)
        self.assertIn("La voz ef-dora no existe", done.stderr)
        self.assertFalse(stentor.pid_file().exists())

    def test_the_reader_writes_its_errors_to_a_log(self) -> None:
        stentor.ensure_state_dir()
        with mock.patch.object(stentor.subprocess, "Popen") as popen, \
                mock.patch.object(stentor, "reader_alive", return_value=1234):
            stentor.start_reader(self.folder / "answer.txt")

        self.assertEqual(Path(popen.call_args.kwargs["stderr"].name), stentor.log_file())


@unittest.skipIf(stentor.WINDOWS, "POSIX permissions")
class PrivateState(Isolated):
    def test_the_state_folder_is_private(self) -> None:
        folder = stentor.ensure_state_dir()

        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)

    def test_a_state_folder_that_is_a_link_is_refused(self) -> None:
        target = self.folder / "elsewhere"
        target.mkdir()
        (self.folder / "state").symlink_to(target)

        with self.assertRaises(SystemExit):
            stentor.ensure_state_dir()


class Housekeeping(Isolated):
    def test_saving_an_answer_removes_answers_older_than_a_week(self) -> None:
        import time

        stentor.ensure_state_dir()
        old = stentor.answer_file("vieja")
        old.write_text("x")
        week_ago = time.time() - 8 * 24 * 3600
        os.utime(old, (week_ago, week_ago))

        stentor.save_answer("s1", "nueva")

        self.assertFalse(old.exists())
        self.assertEqual(stentor.answer_file("s1").read_text(encoding="utf-8"), "nueva")

    def test_dates_are_not_taken_for_paths(self) -> None:
        self.assertEqual(stentor.speakable("el 26/11/2026"), "el 26/11/2026")


class InstallerRepairs(Isolated):
    def test_a_venv_without_pip_is_created_again(self) -> None:
        environment = self.folder / "venv"
        python = install.venv_python(environment)
        python.parent.mkdir(parents=True)
        python.write_text("")
        with mock.patch.object(install, "pip_works", side_effect=[False, True]), \
                mock.patch.object(install.venv, "EnvBuilder") as builder:
            self.assertEqual(install.ensure_venv(environment), python)

        builder.assert_called_once_with(with_pip=True, clear=True)

    def test_a_failed_download_explains_itself(self) -> None:
        import urllib.error

        failure = urllib.error.URLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
        with mock.patch.object(install.urllib.request, "urlopen", side_effect=failure), \
                self.assertRaises(SystemExit) as stopped:
            install.download("m.bin", "0" * 64, self.folder)

        self.assertIn("Install Certificates", str(stopped.exception))
        self.assertFalse(self.folder.joinpath("m.bin.part").exists())

if __name__ == "__main__":
    unittest.main()
