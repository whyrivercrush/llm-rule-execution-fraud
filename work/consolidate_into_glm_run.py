# -*- coding: utf-8 -*-
"""
Consolidate all used project files, datasets and results into
glm_run_2026-09-12/ without touching the existing GLM run artifacts.

Layout added inside glm_run_2026-09-12/:
  glm_handoff_clean/            copy of formal_experiment_archive_for_zcode
  consolidated_project_snapshot/ copy of the project root (excluding this target dir)
  raw_data/                     hardlinks/copies of the external raw dataset directory
  CONSOLIDATION_README.md
  CONSOLIDATION_MANIFEST.csv

API keys are redacted in the copies. Existing files are never overwritten.
"""

import csv
import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
TARGET = PROJECT / "glm_run_2026-09-12"
EXTERNAL_RAW = Path(r"C:\Users\Niconiconi\sci\数据训练集")

SNAPSHOT = TARGET / "consolidated_project_snapshot"
HANDOFF = TARGET / "glm_handoff_clean"
RAW = TARGET / "raw_data"

TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".csv", ".jsonl", ".html", ".toml", ".yaml", ".yml", ".cfg", ".ini"}
SECRET_RE = re.compile(r"sk-[A-Za-z0-9]{16,}")


def safe_write(path: Path, data: bytes, report: list, source: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        report.append((str(source), str(path), "skipped-existing"))
        return
    path.write_bytes(data)
    report.append((str(source), str(path), "copied"))


def redact_text(text: str) -> str:
    text = SECRET_RE.sub("REDACTED_API_KEY", text)
    # JSON api_key values
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "api_key" in obj:
            obj["api_key"] = "REDACTED"
            text = json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return text


def copy_tree_redacted(src_root: Path, dst_root: Path, report: list, skip_under: Path):
    for src in src_root.rglob("*"):
        if not src.is_file():
            continue
        try:
            src.relative_to(skip_under)
            continue  # skip the consolidation target itself
        except ValueError:
            pass
        rel = src.relative_to(src_root)
        if "__pycache__" in rel.parts or src.suffix.lower() == ".pyc":
            continue
        dst = dst_root / rel
        if dst.exists():
            report.append((str(src), str(dst), "skipped-existing"))
            continue
        if src.suffix.lower() in TEXT_SUFFIXES:
            try:
                text = src.read_text(encoding="utf-8")
                cleaned = redact_text(text)
                safe_write(dst, cleaned.encode("utf-8"), report, src)
                if cleaned != text:
                    report.append((str(src), str(dst), "redacted"))
                continue
            except UnicodeDecodeError:
                pass
        safe_write(dst, src.read_bytes(), report, src)


def link_or_copy_ext_raw(report: list):
    RAW.mkdir(parents=True, exist_ok=True)
    if not EXTERNAL_RAW.exists():
        report.append((str(EXTERNAL_RAW), str(RAW), "missing-source"))
        return
    for src in sorted(EXTERNAL_RAW.iterdir()):
        if not src.is_file():
            continue
        dst = RAW / src.name
        if dst.exists():
            report.append((str(src), str(dst), "skipped-existing"))
            continue
        try:
            os.link(src, dst)
            report.append((str(src), str(dst), "hardlink"))
        except Exception:
            shutil.copy2(src, dst)
            report.append((str(src), str(dst), "copied"))


def build_manifest():
    rows = []
    for p in sorted(TARGET.rglob("*")):
        if not p.is_file():
            continue
        stat = p.stat()
        rel = str(p.relative_to(TARGET)).replace("\\", "/")
        if rel.startswith("consolidated_project_snapshot/"):
            category = "project_snapshot"
        elif rel.startswith("glm_handoff_clean/"):
            category = "glm_handoff_clean"
        elif rel.startswith("raw_data/"):
            category = "raw_data"
        else:
            category = "existing_glm_run"
        rows.append({
            "relative_path": rel,
            "category": category,
            "size_bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    out = TARGET / "CONSOLIDATION_MANIFEST.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return out, rows


def main():
    if not TARGET.exists():
        raise FileNotFoundError(TARGET)
    report = []

    # 1) clean GLM handoff copy
    src_handoff = PROJECT / "formal_experiment_archive_for_zcode"
    copy_tree_redacted(src_handoff, HANDOFF, report, skip_under=TARGET)

    # 2) full project snapshot (excluding the consolidation target)
    copy_tree_redacted(PROJECT, SNAPSHOT, report, skip_under=TARGET)

    # 3) external raw datasets
    link_or_copy_ext_raw(report)

    # 4) manifest + readme
    manifest_path, rows = build_manifest()
    readme = f"""# glm_run_2026-09-12 consolidation

Consolidated at: {datetime.now().strftime('%Y-%m-%d %H:%M')}

This folder already contained a GLM run. Nothing existing was overwritten or
deleted. The consolidation added:

- `glm_handoff_clean/` - sanitized GLM/zcode handoff (zero legacy model traces);
- `consolidated_project_snapshot/` - snapshot of the whole project root at
  consolidation time (excludes this target folder, caches and `*.pyc`);
- `raw_data/` - the external raw dataset directory, as hardlinks where the file
  system allows (same volume) and copies otherwise. Original files remain at
  `{EXTERNAL_RAW}`;
- `CONSOLIDATION_MANIFEST.csv` - every file now under this folder, with category,
  size and mtime.

## Safety

- API keys were replaced with `REDACTED` in the copied files. The existing
  `config.json` files that were already in this folder were not modified; treat
  this folder as containing credentials and do not share it without redaction.
- The GLM run files that already existed at the root of this folder are kept
  as-is and marked `existing_glm_run` in the manifest.
- No files were moved or deleted from the original project or data directory.

## Paper .tex

No `.tex` file was found under the project root, Documents, sci or Desktop at
consolidation time. If the paper source is elsewhere, add it here manually.

See the project root `PROJECT_FILE_INDEX.md` for the curated classification and
`PROJECT_FILE_INVENTORY.csv` for the per-file inventory.
"""
    readme_path = TARGET / "CONSOLIDATION_README.md"
    if not readme_path.exists():
        readme_path.write_text(readme, encoding="utf-8", newline="\n")

    # 5) secret scan of newly added subfolders
    leaked = []
    for sub in (HANDOFF, SNAPSHOT, RAW):
        if not sub.exists():
            continue
        for p in sub.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if SECRET_RE.search(text):
                leaked.append(str(p.relative_to(TARGET)))

    print("target:", TARGET)
    print("manifest rows:", len(rows))
    print("handoff files:", sum(1 for r in rows if r["category"] == "glm_handoff_clean"))
    print("snapshot files:", sum(1 for r in rows if r["category"] == "project_snapshot"))
    print("raw data files:", sum(1 for r in rows if r["category"] == "raw_data"))
    print("existing GLM run files:", sum(1 for r in rows if r["category"] == "existing_glm_run"))
    print("secret leaks in added subfolders:", len(leaked))
    if leaked:
        print("  ", leaked[:10])
    print("readme:", readme_path)
    print("manifest:", manifest_path)


if __name__ == "__main__":
    main()
