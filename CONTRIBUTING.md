# Contributing to Stentor

Thanks for helping. Reports from macOS and Windows are especially welcome, because Stentor is only
tested on Linux so far.

## Report a problem

Open an [issue](https://github.com/Arielc17/stentor/issues/new/choose) and include:

- your system and terminal,
- your Claude Code version (`claude --version`) and Python version,
- what you did, what you expected and what happened,
- the last lines of `reader.log` if nothing sounds (see *Where things live* in the README).

## Work on the code

| Part | Where |
| --- | --- |
| Keys, hover mark and settings inside Claude Code | `hooks/register.tsx` |
| Reading, voices and audio players | `scripts/stentor.py` |
| Installer | `scripts/install.py` |
| Tests | `hooks/register.test.tsx`, `tests/test_scripts.py` |

Run the checks before opening a pull request:

```
claude plugin validate --strict .
claude plugin test .
python3 -m unittest discover -s tests
```

The tests never play sound and never download the models.

To try your copy in Claude Code, run `claude --plugin-dir .` from the repository folder.

## Pull requests

- Keep each pull request to one change, with its tests.
- Use [Conventional Commits](https://www.conventionalcommits.org/) (`fix: …`, `feat: …`, `docs: …`).
- Update `README.md`, `README.es.md` and `CHANGELOG.md` when the behavior or a message changes.

By contributing you agree that your work is released under the MIT license.
