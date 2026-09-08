#!/usr/bin/env python3
"""Check Markdown documentation for broken relative links and dead anchors.

Scans README.md and every docs/**/*.md file for Markdown links
(``[text](target)``) and verifies:

1. A relative link (no scheme, e.g. not ``http://...`` or ``mailto:...``)
   resolves to a file that actually exists, relative to the linking file's
   own directory.
2. A link fragment (``file.md#some-heading``, or a same-file ``#some-heading``)
   resolves to a heading that actually exists in the target file, using
   GitHub's own heading-to-anchor slug algorithm, so a link that renders
   correctly on GitHub is checked the same way GitHub would resolve it.

This is a promotion of an ad-hoc verification script written by hand during
Phases 0-2 into a real, reusable, cross-platform tool (pure standard library,
so it runs identically under PowerShell, bash, and CI with no extra
dependency). It deliberately does NOT check external (http/https) links:
that needs a network call, is a different kind of flaky, and is out of scope
for a fast, offline documentation-integrity check.

Usage:
    python scripts/check_docs.py            # check the whole repo
    python scripts/check_docs.py --root .   # equivalent, explicit
    python scripts/check_docs.py -v         # list every link checked, not just failures

Exit code is 0 when every link and anchor resolves, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# Matches Markdown links: [text](target). Deliberately simple — this project's
# docs don't use reference-style links ([text][ref]) or link titles
# ([text](target "title")) with the space-then-quote form, so a full CommonMark
# parser would be a dependency bought for a case that doesn't occur here.
LINK_PATTERN = re.compile(r"\]\(([^)\s]+)\)")

# ATX headings only (# through ######) — this project's docs don't use Setext
# headings (underlined with = or -), so that form is intentionally unhandled.
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")

# Fenced code blocks (``` or ~~~) must be skipped when scanning for headings
# and links — a "#" inside a code sample is not a heading, and a
# "[text](target)" inside one is not a link to check.
FENCE_PATTERN = re.compile(r"^(```|~~~)")


def github_slug(heading_text: str, seen: Counter) -> str:
    """Reproduce GitHub's heading-to-anchor slug algorithm.

    Lowercase, strip inline code backticks and Markdown emphasis markers,
    drop anything that isn't a word character, space, or hyphen, turn spaces
    into hyphens, and disambiguate repeated headings on the same page with a
    ``-1``, ``-2``, ... suffix, exactly as GitHub does.
    """
    text = heading_text.strip()
    text = re.sub(r"`([^`]*)`", r"\1", text)  # `code` -> code
    text = re.sub(r"\*\*([^*]*)\*\*", r"\1", text)  # **bold** -> bold
    text = re.sub(r"\*([^*]*)\*", r"\1", text)  # *italic* -> italic
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)  # strip punctuation
    text = re.sub(r"\s+", "-", text.strip())

    slug = f"{text}-{seen[text]}" if seen[text] else text
    seen[text] += 1
    return slug


@dataclass
class MarkdownFile:
    path: Path
    text: str = field(repr=False)
    lines: list[str] = field(repr=False)
    heading_slugs: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: Path) -> MarkdownFile:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        md = cls(path=path, text=text, lines=lines)
        md.heading_slugs = md._extract_heading_slugs()
        return md

    def _extract_heading_slugs(self) -> set[str]:
        slugs: set[str] = set()
        seen: Counter = Counter()
        in_fence = False
        for line in self.lines:
            if FENCE_PATTERN.match(line.strip()):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            match = HEADING_PATTERN.match(line)
            if match:
                slugs.add(github_slug(match.group(2), seen))
        return slugs

    def iter_links(self) -> list[tuple[int, str]]:
        """Yield (line_number, link_target) for every Markdown link, skipping fenced code."""
        results: list[tuple[int, str]] = []
        in_fence = False
        for line_number, line in enumerate(self.lines, start=1):
            if FENCE_PATTERN.match(line.strip()):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for match in LINK_PATTERN.finditer(line):
                results.append((line_number, match.group(1)))
        return results


@dataclass
class Finding:
    file: Path
    line: int
    target: str
    reason: str

    def format(self, root: Path) -> str:
        try:
            display_path = self.file.relative_to(root)
        except ValueError:
            display_path = self.file
        return f"{display_path}:{self.line}: {self.target!r} - {self.reason}"


def is_external(target: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target)) or target.startswith("//")


def check_file(md: MarkdownFile, root: Path, verbose: bool) -> list[Finding]:
    findings: list[Finding] = []

    for line_number, raw_target in md.iter_links():
        # Strip a trailing Markdown link title, e.g. (target "Title") — this
        # project's docs don't use that form, but strip defensively so a
        # future one doesn't produce a bogus path.
        target = raw_target.split(" ", 1)[0]

        if is_external(target):
            if verbose:
                print(f"  skip (external)  {md.path.relative_to(root)}:{line_number} -> {target}")
            continue

        path_part, _, fragment = target.partition("#")

        if path_part:
            resolved = (md.path.parent / path_part).resolve()
            if not resolved.exists():
                findings.append(
                    Finding(md.path, line_number, target, f"file does not exist: {path_part}")
                )
                continue
            target_md = MarkdownFile.load(resolved) if resolved.suffix.lower() == ".md" else None
        else:
            # A same-file fragment link, e.g. "#some-heading".
            resolved = md.path
            target_md = md

        if fragment and target_md is not None and fragment not in target_md.heading_slugs:
            findings.append(
                Finding(
                    md.path,
                    line_number,
                    target,
                    f"no heading in {resolved.name} produces anchor #{fragment}",
                )
            )
            continue

        if verbose:
            print(f"  ok              {md.path.relative_to(root)}:{line_number} -> {target}")

    return findings


def discover_markdown_files(root: Path) -> list[Path]:
    """Every Markdown file worth link-checking: top-level docs, docs/, and .github/.

    Deliberately NOT a blanket "every *.md in the repo" walk: that would also
    sweep up node_modules/ or a future vendored dependency's own README, whose
    broken links (if any) are not this project's to fix.
    """
    files: list[Path] = []
    for name in ("README.md", "CONTRIBUTING.md"):
        candidate = root / name
        if candidate.exists():
            files.append(candidate)
    for directory in ("docs", ".github"):
        dir_path = root / directory
        if dir_path.is_dir():
            files.extend(sorted(dir_path.rglob("*.md")))
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root to scan (default: parent of this script's directory).",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Print every link checked, not just failures."
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    files = discover_markdown_files(root)

    if not files:
        print(f"No Markdown files found under {root} (expected README.md and/or docs/).")
        return 1

    all_findings: list[Finding] = []
    total_links = 0
    for path in files:
        md = MarkdownFile.load(path)
        if args.verbose:
            print(f"{path.relative_to(root)}:")
        findings = check_file(md, root, args.verbose)
        total_links += len(md.iter_links())
        all_findings.extend(findings)

    print()
    if all_findings:
        print(
            f"FAILED - {len(all_findings)} broken link(s)/anchor(s) out of {total_links} checked:\n"
        )
        for finding in all_findings:
            print(f"  {finding.format(root)}")
        return 1

    print(f"OK - {total_links} link(s) across {len(files)} file(s) all resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
