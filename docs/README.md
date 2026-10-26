# docs/

| Path | What |
|---|---|
| `guides/BUILD.md` | Build the standalone app and the Windows installer. |
| `guides/RELEASING.md` | Cut a release: version bump, tag, CI, appcast, signing. |
| `guides/PANELS.md` | Write your own panel: topics, the panel API, a copy-paste example. |
| `guides/MACOS_HANDOFF.md` | Building and releasing on a Mac. |
| `superpowers/specs/` | Design specs, one per feature, named `YYYY-MM-DD-<topic>-design.md`. |
| `superpowers/plans/` | Implementation plans that executed those specs. |
| `superpowers/notes/` | Findings and preflight notes recorded along the way. |

Specs and plans are dated records: they keep the file paths that were true when
they were written. Some files under `superpowers/` are intentionally kept out of
git, so stage files by name here rather than with `git add -A`.
