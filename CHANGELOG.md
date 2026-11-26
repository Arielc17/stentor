# Changelog

All notable changes to Stentor are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## [1.0.1] - 2026-11-26

### Fixed

- A mistyped name in the Voices setting is now reported ("There is no voice named …") instead of
  silently reading with the default voice.
- The key labels take their language from the first named voice, as the reader does.

## [1.0.0] - 2026-11-26

### Added

- Key `1` reads the answer on screen, `2` stops, `3` switches the voice.
- A **▶ Listen** mark on hover reads any answer.
- Local Kokoro voices in nine languages, with labels in Spanish or English.
- `/stentor:install` and an installer that sets up Python, `kokoro-onnx` and the voice models,
  checking their SHA-256.
- Pauses and resumes music and video players on Linux through MPRIS.
- Voices and speed settings in `/plugin`.

[1.0.1]: https://github.com/Arielc17/stentor/releases/tag/v1.0.1
[1.0.0]: https://github.com/Arielc17/stentor/releases/tag/v1.0.0
