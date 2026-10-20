"""Offline, redacted pre-publication review of Git blobs and publishable files.

Only Python 3.11 stdlib and Git are required. Matches are review candidates,
never a claim that a credential is live. No matching value or source line is
printed, no network is used, and no repository state is changed.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import sys


RULES = {
    "private_key_marker": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "provider_token_shape": re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[A-Z0-9]{16})\b"),
    "credential_url": re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:([^\s/@]+)@", re.I),
    "literal_credential": re.compile(r"(?:password|api[_-]?key|access[_-]?token|secret)\s*[=:]\s*[\"']([^\"'\r\n]{8,})[\"']", re.I),
    "email_review": re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"),
    "mainland_phone_review": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "local_home_path_review": re.compile(r"(?:[A-Za-z]:[\\/]Users[\\/][^\\/\s`\"<>]+|/(?:Users|home)/[^/\s`\"<>]+)"),
}
SENSITIVE_SUFFIXES = {".db", ".db3", ".sqlite", ".sqlite3", ".zip", ".dump", ".bak", ".log", ".pem", ".key", ".p12", ".pfx"}
MEDIA_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4"}
EXAMPLE_DOMAINS = {"example.com", "example.org", "example.net", "localhost.localdomain"}


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise RuntimeError("Git operation failed; inspect the repository locally.")
    return result.stdout


def read_blobs(root: Path, oids: list[str]) -> dict[str, bytes]:
    result = subprocess.run(["git", "-C", str(root), "cat-file", "--batch"],
                            input=("\n".join(oids) + "\n").encode(),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise RuntimeError("Git blob access failed.")
    payload = result.stdout
    position = 0
    contents = {}
    for expected in oids:
        newline = payload.index(b"\n", position)
        oid, kind, raw_size = payload[position:newline].decode().split()
        if oid != expected or kind != "blob":
            raise RuntimeError("Incomplete Git blob access.")
        size = int(raw_size)
        position = newline + 1
        contents[oid] = payload[position:position + size]
        if len(contents[oid]) != size:
            raise RuntimeError("Truncated Git blob.")
        position += size + 1
    return contents


def is_example_email(domain: str) -> bool:
    domain = domain.lower()
    return (domain in EXAMPLE_DOMAINS or domain.endswith((".example", ".invalid", ".test"))
            or domain.startswith("example."))


def categories(data: bytes) -> list[dict[str, object]]:
    if b"\0" in data:
        return []
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return []
    found = []
    for name, pattern in RULES.items():
        lines = set()
        count = 0
        for match in pattern.finditer(content):
            if name == "email_review" and is_example_email(match.group(1)):
                continue
            count += 1
            lines.add(content.count("\n", 0, match.start()) + 1)
        if count:
            found.append({"category": name, "count": count, "lines": sorted(lines)})
    return found


def filename_categories(name: str) -> list[str]:
    path = Path(name)
    result = []
    if path.name.startswith(".env") and path.name != ".env.example":
        result.append("environment_file_review")
    if path.suffix.lower() in SENSITIVE_SUFFIXES or path.name.endswith((".db-wal", ".db-shm", ".sql.gz")):
        result.append("runtime_or_secret_file_review")
    if path.suffix.lower() in MEDIA_SUFFIXES:
        result.append("binary_manual_review")
    return result


def scan(root: Path) -> dict[str, object]:
    commits = git(root, "rev-list", "--all").decode().splitlines()
    refs = git(root, "for-each-ref", "--format=%(refname)").decode().splitlines()
    blobs: dict[str, dict[str, object]] = {}

    def add_tree(treeish: str, location_key: str, location: str) -> None:
        for record in git(root, "ls-tree", "-rz", "--full-tree", treeish).split(b"\0"):
            if not record:
                continue
            header, filename = record.split(b"\t", 1)
            _, kind, oid = header.decode().split()
            if kind != "blob":
                continue
            name = filename.decode("utf-8", errors="replace")
            entry = blobs.setdefault(oid, {"paths": set(), location_key: location})
            entry["paths"].add(name)

    for commit in commits:
        add_tree(commit, "example_commit", commit)

    # rev-list --all visits commit history, but a local tool may store snapshot
    # trees (or direct blobs) in refs as well. These are not sent by an ordinary
    # branch push; inventory them separately instead of claiming they were read.
    non_commit_refs = []
    for ref in refs:
        peeled = git(root, "rev-parse", f"{ref}^{{}}").decode().strip()
        kind = git(root, "cat-file", "-t", peeled).decode().strip()
        if kind == "tree":
            add_tree(peeled, "example_ref", ref)
        elif kind == "blob":
            entry = blobs.setdefault(peeled, {"paths": set(), "example_ref": ref})
            entry["paths"].add(ref)
        else:
            continue
        non_commit_refs.append({"ref": ref, "object_type": kind})

    findings = []
    binary_review = []
    non_utf8_review = []
    for record in git(root, "log", "--all", "--format=%H%x1f%B%x1e").split(b"\x1e"):
        record = record.lstrip(b"\r\n")
        if not record:
            continue
        commit, message = record.split(b"\x1f", 1)
        for hit in categories(message):
            findings.append({"scope": "commit_message", "example_commit": commit.decode(), **hit})
    blob_contents = read_blobs(root, list(blobs))
    for oid, entry in blobs.items():
        paths = sorted(entry["paths"])
        locations = {"scope": "history" if "example_commit" in entry else "non_commit_ref",
                     "blob": oid, "paths": paths}
        locations.update({key: entry[key] for key in ("example_commit", "example_ref") if key in entry})
        data = blob_contents[oid]
        for hit in categories(data):
            findings.append({**locations, **hit})
        for name in paths:
            for category in filename_categories(name):
                target = binary_review if category == "binary_manual_review" else findings
                target.append({**locations, "category": category, "paths": [name]})
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError:
            non_utf8_review.append({**locations, "category": "non_utf8_manual_review"})

    # Include tracked modifications and untracked files Git would allow adding.
    files = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0")
    current_count = 0
    for filename in sorted(set(files)):
        if not filename:
            continue
        name = filename.decode("utf-8", errors="replace")
        path = root / name
        if path.is_symlink():
            findings.append({"scope": "worktree", "paths": [name], "category": "symlink_manual_review"})
            continue
        if not path.is_file():
            continue
        current_count += 1
        locations = {"scope": "worktree", "paths": [name]}
        for hit in categories(path.read_bytes()):
            findings.append({**locations, **hit})
        for category in filename_categories(name):
            target = binary_review if category == "binary_manual_review" else findings
            target.append({**locations, "category": category})

    # Inventory ignored sensitive-looking names without opening their contents.
    # Build/vendor trees are excluded to keep local runs bounded and relevant.
    ignored = []
    for filename in git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z").split(b"\0"):
        if not filename:
            continue
        name = filename.decode("utf-8", errors="replace")
        if any(part in {"node_modules", ".venv", ".next", "__pycache__", ".git"} for part in Path(name).parts):
            continue
        kinds = filename_categories(name)
        if kinds:
            ignored.append({"paths": [name], "categories": kinds})

    return {
        "schema_version": 2,
        "scope": "all local refs reachable commits and non-commit tree/blob refs plus publishable worktree; ignored names only",
        "limitations": ["Heuristic review; matching strings may be synthetic fixtures.",
                        "No OCR or PDF extraction; manually inspect listed binaries.",
                        "Does not inspect remote-only refs, reflog/unreachable objects, encrypted archives, or external backups.",
                        "Git identity metadata requires owner review; no author emails are printed."],
        "refs": refs, "non_commit_refs": non_commit_refs,
        "commit_count": len(commits), "unique_blob_count": len(blobs),
        "commit_reachable_blob_count": sum("example_commit" in entry for entry in blobs.values()),
        "non_commit_only_blob_count": sum("example_commit" not in entry for entry in blobs.values()),
        "publishable_file_count": current_count,
        "finding_counts": dict(sorted(Counter(str(hit["category"]) for hit in findings).items())),
        "findings": findings, "binary_manual_review": binary_review,
        "non_utf8_manual_review": non_utf8_review, "ignored_name_inventory": ignored,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, help="Optional local redacted JSON report path")
    args = parser.parse_args()
    report = scan(args.root.resolve())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {key: report[key] for key in ("commit_count", "unique_blob_count", "publishable_file_count", "finding_counts")}
    summary["binary_review_entries"] = len(report["binary_manual_review"])
    summary["ignored_name_entries"] = len(report["ignored_name_inventory"])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("Review the redacted locations in --output; completion is not security clearance.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError):
        print("Scan failed; repository/file access was incomplete. No clearance was produced.", file=sys.stderr)
        sys.exit(1)
