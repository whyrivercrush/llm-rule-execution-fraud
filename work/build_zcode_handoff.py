# -*- coding: utf-8 -*-
"""
Build a DeepSeek-free handoff copy of the archive for the zcode/GLM workspace.

- copies the existing archive
- replaces every legacy endpoint/model identifier with a neutral placeholder
- overwrites the GLM execution instruction and config template
- enforces zero 'deepseek' occurrences and zero API-key patterns
"""

import hashlib
import re
import shutil
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "formal_experiment_archive"
DST = PROJECT / "formal_experiment_archive_for_zcode"

TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".csv", ".jsonl", ".html", ".toml", ".cfg", ".ini"}


def sanitize(text: str) -> str:
    replacements = [
        (r"https://api\.deepseek\.com/v1", "legacy-endpoint-removed"),
        (r"api\.deepseek\.com", "legacy-endpoint-removed"),
        (r"deepseek-chat", "legacy-model"),
        (r"deepseek", "legacy-model"),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    return text


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write(rel: str, text: str) -> None:
    path = DST / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def main():
    if not SRC.exists():
        raise FileNotFoundError(f"source archive missing: {SRC}")
    if DST.exists():
        resolved = DST.resolve()
        if resolved.parent != PROJECT.resolve() or resolved.name != "formal_experiment_archive_for_zcode":
            raise RuntimeError(f"refusing to clear unexpected path: {resolved}")
        shutil.rmtree(resolved)
    shutil.copytree(SRC, DST)

    # Sanitize every text file (JSON validity is preserved because only values change).
    sanitized_count = 0
    for path in DST.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            original = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        cleaned = sanitize(original)
        if cleaned != original:
            path.write_text(cleaned, encoding="utf-8", newline="\n")
            sanitized_count += 1

    glm_instruction = """# GLM / zcode execution instruction

You are an EXECUTOR, not the experiment designer.

## IRON RULE 0 — FROZEN PROMPTS (read first, non-negotiable)

The expert-group prompt templates in `original_prompts/` are FROZEN. Your job is
to load them from the archive files and send them byte-for-byte.

You must NOT:
- generate, regenerate, rewrite, reword, summarize, translate, compress or
  "optimize" any expert/role/system prompt;
- ask GLM/zcode to design roles, personas, chains, agents, or to replace the
  archived role definitions;
- substitute the platform's own default agent/system prompt for the archived
  templates;
- merge prompts, reorder prompt modules, or change wording, punctuation,
  whitespace, or the JSON output schema.

The only permitted substitutions are exactly those the archived code performs:
- insert the sample's `card_text` into the fixed user template;
- for Condition C, insert the four frozen rules through the existing code path;
- for Condition D, insert each frozen role rule through `ROLE_RULE_MAP`;
- pass the role analyses into the fixed Decision Maker template.

Mandatory gate before the full run:

    python experiment_code/other_required_scripts/verify_prompt_integrity.py

It must print `RESULT: PASS`. If it prints FAIL, STOP. Do not run the
experiment and do not "fix" the prompts by regenerating them; restore the
archive files instead.

## Hard constraints

1. Do not rewrite, reword, or "improve" any prompt under `original_prompts/`.
2. Do not change role definitions or add/remove roles.
3. Do not modify the rules in `rules/rule_base.txt`.
4. Do not change the A/B/C/D condition definitions in
   `experiment_definition/ABCD_conditions.txt`.
5. Execute only against the GLM/zcode endpoint configured for this workspace:
   base_url `https://open.bigmodel.cn/api/paas/v4/`.
   Keep temperature=0.0, the JSON-mode setting and max_tokens consistent with
   the protocol. Never substitute another API endpoint.

## Starting procedure

1. Read every file in this archive first, especially:
   - `experiment_definition/ABCD_conditions.txt`
   - `execution_record/known_issues.txt`
   - `experiment_code/run_experiment.py`
2. Verify the B/D isolation fix in the archived code:
   - `_run_roles(..., condition, ...)` takes `condition`;
   - `condition == "D"` uses `ROLE_RULE_MAP`; otherwise `[]`.
   A dry run must print `Condition B | Role ... | Rules []` and
   `Condition D | Role ... | Rules ['rule_...']`.
3. Create `config.json` from
   `experiment_code/other_required_scripts/config.example.json`
   with your zcode/GLM credentials and model name.
   Never commit or share the API key.
4. Confirm GLM parameter compatibility with a 2-3 sample smoke test before the
   full run:
   - verify the model accepts the request body used by the client;
   - if `response_format={"type":"json_object"}` is not supported, set
     `"json_mode": false` and rely on the client's JSON extractor;
   - if the model defaults to thinking mode and you need non-thinking parity,
     use the workspace's supported switch or a non-thinking GLM model.
5. Rerun the FULL formal experiment:
   `python src/run_experiment.py --config config.json --concurrency 3
    --sample-concurrency 4 --log-prefix glm_run --resume`
   - 400 samples -> A 400 + B 1600 + C 400 + D 1600 = 4,000 calls.
   - If it stops, top up and rerun the SAME command; `--resume` skips
     already-successful TransactionIDs.
6. Recompute metrics:
   `python src/eval_metrics.py --log-prefix glm_run --bootstrap 1000`
7. Rerun the variance probe:
   `python src/variance_probe.py --config config.json --log-dir logs
    --results-dir results --repeats 5 --sample-concurrency 4`
   - 50 samples x 5 repeats x 10 calls = 2,500 calls.
8. Report GLM results as a cross-model replication. Do not mix them with the
   historical non-GLM metrics as if they came from the same model.

## What NOT to do

- Do not reuse any historical endpoint or model value from result files.
- Do not rename A/B/C/D or reinterpret them.
- Do not add new conditions, roles, rules, or prompt modules.
- Do not change temperature, prompts, dataset or conditions to chase numbers.
- Do not delete old logs; write new GLM logs alongside them.
"""
    write("GLM_execution_instruction.md", glm_instruction)

    readme = """# formal_experiment_archive_for_zcode

This is the clean handoff copy for the zcode/GLM workspace.

> **IRON RULE 0**: the expert-group prompts are frozen. Load and send the
> archived templates byte-for-byte; never regenerate, rewrite or replace them.
> Run `experiment_code/other_required_scripts/verify_prompt_integrity.py`
> before any experiment and require PASS.

- Every legacy endpoint/model identifier from the original audit archive has
  been replaced with a neutral placeholder. Numeric results are unchanged.
- Execute only with the GLM template:
  `experiment_code/other_required_scripts/config.example.json`
  (base_url `https://open.bigmodel.cn/api/paas/v4/`).
- The fixed prompt templates, role definitions, rules and A/B/C/D definitions
  are unchanged from the formal experiment.
- The B/D isolation fix is already present in the archived code.
- Historical metrics are provided only for comparison; they were produced by a
  different model and are sanitized. Treat the GLM run as a cross-model
  replication, not as a re-run of the same model.

See `GLM_execution_instruction.md` for the exact run procedure and call counts.
"""
    write("README.md", readme)

    handoff_note = """# Model handoff note

1. This handoff copy contains NO legacy endpoint or model identifiers.
2. `experiment_code/other_required_scripts/config.example.json` is the only
   execution config template. Fill in the zcode/GLM key and model name.
3. Historical result files were sanitized: model/endpoint values were replaced
   by neutral placeholders; all metric numbers are untouched.
4. If a historical artifact is referenced for comparison, label it explicitly
   as a non-GLM historical run.
5. No API calls were made while preparing this handoff.
"""
    write("MODEL_HANDOFF_NOTE.md", handoff_note)

    iron_rule = """# IRON RULE 0 - FROZEN PROMPTS (read this first)

The expert-group prompt templates in `original_prompts/` are FROZEN.

1. Load the archived prompt files/code constants and send them byte-for-byte.
2. NEVER generate, regenerate, rewrite, reword, summarize, translate, compress
   or "optimize" an expert/role/system prompt.
3. NEVER ask GLM/zcode to design roles, personas, chains, or agents, and NEVER
   replace the archived templates with a platform default system prompt.
4. NEVER merge prompts, reorder prompt modules, or change wording, punctuation,
   whitespace or the JSON schema instructions.
5. Only these substitutions are allowed: inserting the sample card_text;
   Condition C inserting the four frozen rules through the existing code path;
   Condition D inserting the frozen per-role rules via ROLE_RULE_MAP; passing
   role analyses into the frozen Decision Maker template.

Mandatory verification before the full run:

    python experiment_code/other_required_scripts/verify_prompt_integrity.py

Require `RESULT: PASS`. On FAIL, stop; restore the archive templates instead of
regenerating them.
"""
    write("PROMPT_FREEZE_IRON_RULE.md", iron_rule)

    # Final safety gate.
    bad = []
    for path in DST.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if re.search(r"deepseek|api\.deepseek\.com", text, flags=re.IGNORECASE):
            bad.append(str(path.relative_to(DST)))
        if re.search(r"sk-[A-Za-z0-9]{16,}", text):
            bad.append(str(path.relative_to(DST)) + " [api-key-pattern]")
    if bad:
        raise RuntimeError("handoff still contains forbidden traces:\n" + "\n".join(bad))

    files = [p for p in DST.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    print("handoff dir:", DST)
    print("files:", len(files))
    print(f"size: {total / 1024 / 1024:.2f} MB")
    print("sanitized text files:", sanitized_count)
    print("safety gate: zero legacy-endpoint/model traces, zero sk- key patterns")
    print("instruction:", DST / "GLM_execution_instruction.md")


if __name__ == "__main__":
    main()
