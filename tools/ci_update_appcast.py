"""Insert new appcast <item> entries for a release. CI-only helper.

Called once by .github/workflows/release.yml, after both platform builds are
signed, to add matching Windows + macOS <item>s to appcast.xml in one release.
Text-based insertion (not an XML tree round-trip through ElementTree) so the
file's existing comments, indentation, and CDATA blocks come out
byte-identical except for the new content prepended to it.

Not meant to be run by hand for a manual release — see RELEASING.md, which
still documents editing appcast.xml directly for that case.
"""

from __future__ import annotations

import argparse
import html
from email.utils import formatdate
from pathlib import Path

_CSS = """        <style>
          html, body { background:#ffffff; color:#141414; margin:0;
                       padding:10px 12px; font-family:'Segoe UI', Arial, sans-serif;
                       font-size:13px; line-height:1.4; }
          h3 { margin:0 0 8px; font-size:15px; color:#111111; }
          ul { margin:0; padding-left:20px; }
          li { margin:4px 0; }
        </style>"""

_INSTALLER_ARGS_LINE = (
    '        sparkle:installerArguments='
    '"/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /FORCECLOSEAPPLICATIONS"\n'
)


def _notes_to_html(notes: str) -> str:
    lines = [ln.strip("-* \t") for ln in notes.strip().splitlines() if ln.strip()]
    if not lines:
        lines = ["See the GitHub release for details."]
    return "\n".join(f"          <li>{html.escape(ln)}</li>" for ln in lines)


def _render_item(
    *, version, notes_html, pubdate, url, os_name, length, signature, mime,
    installer_arg_line="",
) -> str:
    return (
        "    <item>\n"
        f"      <title>Aurantium {version}</title>\n"
        "      <description><![CDATA[\n"
        f"{_CSS}\n"
        f"        <h3>{version}</h3>\n"
        "        <ul>\n"
        f"{notes_html}\n"
        "        </ul>\n"
        "      ]]></description>\n"
        f"      <pubDate>{pubdate}</pubDate>\n"
        "      <enclosure\n"
        f'        url="{url}"\n'
        f'        sparkle:version="{version}"\n'
        f'        sparkle:os="{os_name}"\n'
        f"{installer_arg_line}"
        f'        length="{length}"\n'
        f'        sparkle:edSignature="{signature}"\n'
        f'        type="{mime}" />\n'
        "    </item>\n"
    )


def build_items(args: argparse.Namespace, pubdate: str, notes_html: str) -> str:
    items = ""
    if args.windows_url:
        if not (args.windows_length and args.windows_sig):
            raise SystemExit("--windows-url given without --windows-length/--windows-sig")
        items += _render_item(
            version=args.version, notes_html=notes_html, pubdate=pubdate,
            url=args.windows_url, os_name="windows",
            length=args.windows_length, signature=args.windows_sig,
            mime="application/octet-stream",
            installer_arg_line=_INSTALLER_ARGS_LINE,
        )
    if args.macos_url:
        if not (args.macos_length and args.macos_sig):
            raise SystemExit("--macos-url given without --macos-length/--macos-sig")
        items += _render_item(
            version=args.version, notes_html=notes_html, pubdate=pubdate,
            url=args.macos_url, os_name="macos",
            length=args.macos_length, signature=args.macos_sig,
            mime="application/zip",
        )
    if not items:
        raise SystemExit("Nothing to add: pass at least one of --windows-url / --macos-url")
    return items


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--appcast", required=True, type=Path)
    p.add_argument("--version", required=True)
    p.add_argument("--notes-file", required=True, type=Path)
    p.add_argument("--pubdate", help="RFC-822 date; defaults to now (UTC)")
    p.add_argument("--windows-url")
    p.add_argument("--windows-length")
    p.add_argument("--windows-sig")
    p.add_argument("--macos-url")
    p.add_argument("--macos-length")
    p.add_argument("--macos-sig")
    args = p.parse_args()

    pubdate = args.pubdate or formatdate(usegmt=True)
    notes_html = _notes_to_html(args.notes_file.read_text(encoding="utf-8"))
    new_items = build_items(args, pubdate, notes_html)

    text = args.appcast.read_text(encoding="utf-8")
    marker = "\n    <item>"
    idx = text.index(marker)  # first existing <item>; new ones go right before it
    new_text = text[:idx] + "\n" + new_items + text[idx + 1 :]
    args.appcast.write_text(new_text, encoding="utf-8")
    print(f"Inserted {new_items.count('<item>')} item(s) for version {args.version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
