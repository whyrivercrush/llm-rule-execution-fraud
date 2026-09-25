# -*- coding: utf-8 -*-
"""Generate PROJECT_FILE_INDEX.md and PROJECT_FILE_INVENTORY.csv."""

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
EXTERNAL_RAW = Path(r"C:\Users\Niconiconi\sci\数据训练集")


OVERRIDES = {
    "config.json": (
        "Live runtime config (contains a real API key).",
        "CURRENT (local only; never share/commit)", "runtime",
    ),
    "config.example.json": (
        "GLM/zcode config template: BigModel OpenAI-compatible endpoint + placeholders.",
        "CURRENT template", "zcode/GLM handoff",
    ),
    "README.md": ("Project run/eval/formal workflow instructions.", "CURRENT", "docs"),
    ".gitignore": ("Ignores config.json and raw logs to protect secrets.", "CURRENT", "repo hygiene"),
    "src/run_experiment.py": ("Main A/B/C/D inference runner (B/D isolation fixed).", "CURRENT", "code"),
    "src/eval_metrics.py": ("Main-track metrics, Bootstrap and McNemar evaluation.", "CURRENT", "code"),
    "src/sample_formal_test.py": ("Builds the 400-sample formal blind test set (seed=100).", "CURRENT", "code"),
    "src/variance_probe.py": ("50x5 variance probe runner.", "CURRENT", "code"),
    "src/check_determinism.py": ("Repeated-call determinism probe for the configured endpoint.", "CURRENT", "code"),
    "src/eval_corrected_B.py": ("Corrected-B metrics and McNemar/Bootstrap comparisons.", "CURRENT", "code"),
    "src/verify_prompt_integrity.py": ("Machine check that frozen prompt templates match the fingerprint.", "CURRENT", "code"),
    "logs/formal_run_condition_A.jsonl": ("Formal 2026-09-10 Condition A, 400 records.", "VALID main-track A", "2026-09-10 formal"),
    "logs/formal_run_condition_B.jsonl": ("Formal 2026-09-10 Condition B.", "INVALID (rules wrongly injected); audit only", "2026-09-10 formal"),
    "logs/formal_run_condition_C.jsonl": ("Formal 2026-09-10 Condition C, 400 records.", "VALID main-track C", "2026-09-10 formal"),
    "logs/formal_run_condition_D.jsonl": ("Formal 2026-09-10 Condition D, 400 records.", "VALID main-track D", "2026-09-10 formal"),
    "logs/pilot_run_condition_B_corrected.jsonl": ("Corrected Condition B (roles only, empty rules), 400 records.", "CURRENT corrected B (canonical name)", "2026-09-12 corrected-B"),
    "logs/pilot_run_correctedB_condition_B.jsonl": ("File written directly by the corrected-B rerun; byte-identical to the canonical copy.", "CURRENT corrected B (raw output)", "2026-09-12 corrected-B"),
    "results/corrected_B_metrics.csv": ("A/B_corrected/B_old/C/D metrics incl. MCC and positive-class F1.", "CURRENT for corrected-B comparison", "2026-09-12"),
    "results/corrected_B_pairwise_tests.csv": ("Corrected-B vs A/C/D and old-B vs A/C/D: paired Bootstrap + McNemar.", "CURRENT for corrected-B comparison", "2026-09-12"),
    "results/corrected_B_results.md": ("Replacement block for all old Condition-B paper numbers.", "CURRENT", "2026-09-12"),
    "results/formal_metrics_summary.csv": ("Formal main metrics. A/C/D valid; B row is the invalid pre-fix B.", "PARTIAL: B row INVALID", "2026-09-10 formal"),
    "results/formal_metrics_bootstrap.json": ("Formal Bootstrap CIs. A/C/D valid; B entries invalid.", "PARTIAL: B entries INVALID", "2026-09-10 formal"),
    "results/formal_pairwise_tests.csv": ("Formal pairwise tests. Rows involving old B are invalid.", "PARTIAL: B rows INVALID", "2026-09-10 formal"),
    "results/formal_predictions_detail.csv": ("Per-sample wide predictions. B columns come from invalid old B.", "PARTIAL: B columns INVALID", "2026-09-10 formal"),
    "results/model_variance_50samples.json": ("Canonical variance report: A/C/D original + corrected B.", "CURRENT canonical", "2026-09-10 + corrected B 2026-09-12"),
    "results/model_variance_50samples_prerefix.json": ("Pre-fix variance report with invalid B column.", "HISTORY ONLY: B INVALID", "2026-09-10"),
    "results/variance_run_metrics.csv": ("Per-repeat variance metrics; B rows replaced by corrected B.", "CURRENT canonical", "mixed batches"),
    "results/variance_run_metrics_prerefix.csv": ("Pre-fix per-repeat metrics; B rows invalid.", "HISTORY ONLY: B INVALID", "2026-09-10"),
    "results/model_variance_B_correction_note.md": ("Documents the corrected-B variance merge and provenance.", "CURRENT", "2026-09-12"),
    "results/formal_call_audit.csv": ("Call-count audit: main 4,000, corrected B 1,600, variance 2,500.", "CURRENT audit", "2026-09-12"),
    "results/formal_execution_report.md": ("Dual-track execution report; contains a correction banner for old B.", "CURRENT with correction notice", "2026-09-10/12"),
    "rerun_2026-09-12/RERUN_REPORT.md": ("Full fixed-code rerun report. IMPORTANT: this batch used a legacy endpoint/model, not GLM.", "VALID legacy-model rerun; NOT GLM evidence", "2026-09-12 legacy rerun"),
    "rerun_2026-09-12/config.json": ("Legacy endpoint/model config used by the rerun. Do not reuse for GLM.", "HISTORY ONLY: wrong endpoint for GLM", "2026-09-12 legacy rerun"),
    "formal_experiment_archive/README.md": ("Raw audit archive overview; contains a warning not to hand it to zcode.", "AUDIT ARCHIVE (raw)", "2026-09-12"),
    "formal_experiment_archive_for_zcode/GLM_execution_instruction.md": ("Zcode/GLM handoff instruction with IRON RULE 0 and GLM endpoint.", "CURRENT handoff", "2026-09-12"),
    "formal_experiment_archive_for_zcode/PROMPT_FREEZE_IRON_RULE.md": ("Standalone frozen-prompt iron rule for zcode.", "CURRENT handoff", "2026-09-12"),
    "formal_experiment_archive_for_zcode/experiment_code/other_required_scripts/verify_prompt_integrity.py": ("Machine verifier for frozen prompt fingerprints.", "CURRENT handoff", "2026-09-12"),
    "formal_experiment_archive_for_zcode/experiment_code/other_required_scripts/config.example.json": ("GLM BigModel config template for zcode.", "CURRENT handoff", "2026-09-12"),
    "data/test/test_cards_400.json": ("Formal 400-sample test set (200/200, seed=100, pilot IDs excluded).", "CURRENT canonical", "2026-09-10"),
    "data/test/test_cards_400_meta.json": ("Sampling metadata for the formal test set.", "CURRENT", "2026-09-10"),
    "B_isolation_evidence/04_comparison_summary.md": ("B/D isolation evidence summary.", "CURRENT audit evidence", "2026-09-12"),
    "glm_run_2026-09-12/CONSOLIDATION_README.md": ("Consolidation README: what was merged, GLM run status, credentials warning.", "CURRENT", "2026-09-16 consolidation"),
    "glm_run_2026-09-12/CONSOLIDATION_MANIFEST.csv": ("Per-file manifest of the consolidated GLM run bundle.", "CURRENT", "2026-09-16 consolidation"),
    "glm_run_2026-09-12/logs/glm_run_condition_A.jsonl": ("GLM main run Condition A, 400 records.", "CURRENT GLM result", "GLM run"),
    "glm_run_2026-09-12/logs/glm_run_condition_B.jsonl": ("GLM main run Condition B, 400 records.", "CURRENT GLM result", "GLM run"),
    "glm_run_2026-09-12/logs/glm_run_condition_C.jsonl": ("GLM main run Condition C, 400 records.", "CURRENT GLM result", "GLM run"),
    "glm_run_2026-09-12/logs/glm_run_condition_D.jsonl": ("GLM main run Condition D, 400 records.", "CURRENT GLM result", "GLM run"),
    "glm_run_2026-09-12/config.json": ("GLM run config (may contain a live key; do not share unredacted).", "CURRENT GLM run (secret risk)", "GLM run"),
}


def classify(rel: str):
    parts = rel.split("/")
    top = parts[0]
    if top == "src":
        return "code", "source code"
    if top == "work":
        return "code", "auxiliary/build scripts"
    if top == "logs":
        return "logs", "experiment logs"
    if top == "results":
        return "results", "metrics/results"
    if top == "data":
        return "processed_data", "processed/sampled data"
    if top == "formal_experiment_archive":
        return "audit", "raw audit archive"
    if top == "formal_experiment_archive_for_zcode":
        return "handoff", "zcode/GLM handoff"
    if top == "glm_run_2026-09-12":
        return "glm_run_bundle", "GLM run + consolidated snapshot/raw data"
    if top == "B_isolation_evidence":
        return "audit", "B isolation evidence"
    if top == "rerun_2026-09-12":
        return "rerun", "legacy-model fixed rerun"
    if top == "outputs":
        return "docs", "research reports"
    if top in {".agents", ".codex"}:
        return "other", "workspace environment"
    if "__pycache__" in parts:
        return "other", "python cache"
    if rel.endswith(".json") and rel.startswith("config"):
        return "config", "configuration"
    if rel.endswith(".md"):
        return "docs", "documentation"
    if rel.endswith(".json") and not top:
        return "config", "root configuration"
    return "other", "misc"


def guess_batch(rel: str, mtime: float):
    if rel.startswith("rerun_2026-09-12"):
        return "2026-09-12 legacy-model rerun"
    if "corrected" in rel.lower() or "correction" in rel.lower():
        return "2026-09-12 corrected-B"
    if rel.startswith("logs/formal_run") or rel.startswith("results/formal_") or rel.startswith("data/test"):
        return "2026-09-10 formal"
    if rel.startswith("logs/variance") or rel.startswith("results/variance") or "model_variance" in rel:
        return "2026-09-10 variance probe"
    if rel.startswith("logs/sanity") or rel.startswith("logs/pilot_run_condition"):
        return "2026-09-10 smoke/pilot"
    if rel.startswith(("formal_experiment_archive", "B_isolation_evidence")):
        return "2026-09-12 audit/handoff"
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")


def guess_status(rel: str):
    if rel == "logs/formal_run_condition_B.jsonl":
        return "INVALID: old B with rules injected; audit only"
    if rel in {"logs/variance_r%d_condition_B.jsonl" % i for i in range(1, 6)}:
        return "INVALID: pre-fix variance B; do not use"
    if rel in {"logs/sanity_condition_B.jsonl", "logs/pilot_run_condition_B.jsonl"}:
        return "HISTORY: pre-fix B smoke/pilot; not for results"
    if rel.startswith("rerun_2026-09-12"):
        return "HISTORY/CROSS-CHECK: valid fixed-code legacy-model rerun, NOT GLM"
    if rel.startswith("formal_experiment_archive_for_zcode"):
        return "CURRENT handoff (zero legacy traces)"
    if rel.startswith("formal_experiment_archive"):
        return "AUDIT ARCHIVE (raw, do not hand to zcode)"
    if rel.startswith("work/"):
        return "AUXILIARY (build/verify; not experiment runtime)"
    if "__pycache__" in rel:
        return "GENERATED cache"
    if rel.startswith(("logs/", "results/", "data/")):
        if "prerefix" in rel:
            return "HISTORY: pre-fix backup"
        return "CURRENT"
    if rel.startswith("src/"):
        return "CURRENT code"
    return "CURRENT" if rel in OVERRIDES else "REFERENCE"


def describe(rel: str, category: str, subcategory: str, mtime: float):
    if rel in OVERRIDES:
        return OVERRIDES[rel][0], OVERRIDES[rel][1], OVERRIDES[rel][2]
    if category == "logs" and rel.endswith(".jsonl"):
        return f"Experiment log: {Path(rel).name}", guess_status(rel), guess_batch(rel, mtime)
    if category == "results":
        status = "HISTORY: pre-fix backup" if "prerefix" in rel else "CURRENT"
        return f"Result/metric file: {Path(rel).name}", status, guess_batch(rel, mtime)
    if category == "handoff":
        return f"Handoff copy of {rel.split('/', 1)[1]}", "CURRENT handoff", "handoff"
    if category == "audit":
        return f"Audit artifact: {rel}", "AUDIT", "audit"
    if category == "rerun":
        return f"Legacy-model rerun artifact: {rel.split('/', 1)[1]}", "HISTORY/CROSS-CHECK", "rerun"
    return f"{subcategory}: {Path(rel).name}", guess_status(rel), guess_batch(rel, mtime)


def line_count(path: Path):
    if path.suffix.lower() != ".jsonl":
        return ""
    try:
        return sum(1 for _ in path.open(encoding="utf-8"))
    except Exception:
        return ""


def md_table(rows, headers):
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(row.get(h, "")) for h in headers) + " |")
    return "\n".join(out)


def main():
    files = [p for p in PROJECT.rglob("*") if p.is_file()]
    rows = []
    for p in files:
        rel = str(p.relative_to(PROJECT)).replace("\\", "/")
        stat = p.stat()
        category, subcategory = classify(rel)
        desc, status, batch = describe(rel, category, subcategory, stat.st_mtime)
        rows.append({
            "path": rel,
            "category": category,
            "subcategory": subcategory,
            "size_bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "batch": batch,
            "status": status,
            "description": desc,
            "jsonl_records": line_count(p) if category == "logs" else "",
        })

    # External raw data
    external = []
    if EXTERNAL_RAW.exists():
        for p in sorted(EXTERNAL_RAW.iterdir()):
            if p.is_file():
                stat = p.stat()
                external.append({
                    "path": str(p),
                    "category": "raw_data_external",
                    "subcategory": "raw dataset",
                    "size_bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "batch": "raw dataset",
                    "status": "EXTERNAL raw data (read-only reference)",
                    "description": f"Raw dataset/script: {p.name}",
                    "jsonl_records": "",
                })
    all_rows = rows + external
    with (PROJECT / "PROJECT_FILE_INVENTORY.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_rows)

    def rows_of(*keys):
        return [r for r in rows if r["category"] in keys]

    code_rows = rows_of("code")
    config_rows = rows_of("config")
    data_rows = rows_of("processed_data")
    log_rows = rows_of("logs")
    result_rows = rows_of("results")
    doc_rows = rows_of("docs")
    audit_rows = rows_of("audit")
    handoff_rows = rows_of("handoff")
    rerun_rows = rows_of("rerun")

    counts = Counter(r["category"] for r in rows)
    md = []
    md.append("# PROJECT FILE INDEX\n")
    md.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}  ")
    md.append(f"Project root: `{PROJECT}`  ")
    md.append(f"Files under project root: **{len(rows)}** (plus {len(external)} external raw-data files).\n")
    md.append("A machine-readable per-file inventory is in `PROJECT_FILE_INVENTORY.csv`.\n")

    md.append("## 重点文件（置顶）\n")
    md.append("### 1) 论文 Chapter 4/5 的权威数据来源\n")
    md.append("""**主轨（single-run, N=400）**

| 条件 | 权威日志 | 状态 |
| --- | --- | --- |
| A | `logs/formal_run_condition_A.jsonl` | 有效（2026-09-10） |
| B | `logs/pilot_run_condition_B_corrected.jsonl` | 有效修正版 B（2026-09-12，纯角色无规则） |
| C | `logs/formal_run_condition_C.jsonl` | 有效（2026-09-10） |
| D | `logs/formal_run_condition_D.jsonl` | 有效（2026-09-10） |

**主轨指标/检验（必须使用修正 B）**

- 修正 B 指标（含 MCC、正类 F1）：`results/corrected_B_metrics.csv`
- 修正 B vs A/C/D 的配对 Bootstrap + McNemar：`results/corrected_B_pairwise_tests.csv`
- 可直接替换论文旧 B 的说明块：`results/corrected_B_results.md`
- A/C/D 的正式指标可引用 `results/formal_metrics_summary.csv` 中相应行；**B 行无效**。
- 注意：目前尚不存在一份“A + B_corrected + C + D 全部合并、含全部配对检验”的单一最终表；建议定稿前生成一份 consolidated 文件。

**副轨方差（50×5）**

- 标准报告：`results/model_variance_50samples.json`（B 已替换为修正版；A/C/D 保持原始有效值）
- 逐轮指标：`results/variance_run_metrics.csv`（B 行已替换）
- 修正说明：`results/model_variance_B_correction_note.md`
- 修复前备份（仅审计）：`results/model_variance_50samples_prerefix.json`、`results/variance_run_metrics_prerefix.csv`

**审计与诚信证据**

- 全条件逐记录审计：`formal_experiment_archive/execution_record/ABCD_integrity_audit/00_summary.csv` 与 `01_rule_distribution_and_prompt_audit.csv`
- B 隔离专项证据：`B_isolation_evidence/04_comparison_summary.md`
- 调用次数审计：`results/formal_call_audit.csv`

### 2) 已确认有问题、不可用于正式结果的旧 B 产出

- `logs/formal_run_condition_B.jsonl`（400/400 条错误注入 D 的 per-role 规则）
- `logs/variance_r1..r5_condition_B.jsonl`（旧方差 B）
- `logs/sanity_condition_B.jsonl`、`logs/pilot_run_condition_B.jsonl`（修复前小样本 B）
- `results/formal_metrics_summary.csv` 的 B 行
- `results/formal_metrics_bootstrap.json` 的 B 项
- `results/formal_pairwise_tests.csv` 中所有涉及旧 B 的行
- `results/formal_predictions_detail.csv` 的 B 列
- `results/model_variance_50samples_prerefix.json`、`results/variance_run_metrics_prerefix.csv`
- `results/pilot_metrics_*.csv/json`（5 条 pilot，修复前；仅历史）

### 3) 论文 .tex 最新版本

- **在项目根目录、`C:\\Users\\Niconiconi\\Documents`、`C:\\Users\\Niconiconi\\sci`、`C:\\Users\\Niconiconi\\Desktop` 中均未找到 `.tex` 文件。**
- 因此当前机器上无法指定“最新定稿 tex 路径”。若论文在其他位置或另一台机器，请把路径补进本索引。

### 4) zcode/GLM 交接

- 交接目录：`formal_experiment_archive_for_zcode/`
- 铁律：`formal_experiment_archive_for_zcode/PROMPT_FREEZE_IRON_RULE.md`
- 执行说明：`formal_experiment_archive_for_zcode/GLM_execution_instruction.md`
- GLM 配置模板：`formal_experiment_archive_for_zcode/experiment_code/other_required_scripts/config.example.json`
- Prompt 完整性校验：`formal_experiment_archive_for_zcode/experiment_code/other_required_scripts/verify_prompt_integrity.py`
- 该目录零 DeepSeek 字迹、零密钥模式；原始审计档案 `formal_experiment_archive/` 不要交给 zcode。
""")

    md.append("\n## 1. 代码与脚本\n")
    md.append(md_table(
        [{"path": r["path"], "description": r["description"], "batch": r["batch"], "status": r["status"]} for r in code_rows],
        ["path", "description", "batch", "status"],
    ))
    md.append("\n## 2. 配置文件\n")
    md.append(md_table(
        [{"path": r["path"], "description": r["description"], "status": r["status"]} for r in config_rows],
        ["path", "description", "status"],
    ))
    md.append("\n## 3. 处理后的数据/抽样结果\n")
    md.append(md_table(
        [{"path": r["path"], "description": r["description"], "batch": r["batch"], "status": r["status"]} for r in data_rows],
        ["path", "description", "batch", "status"],
    ))
    md.append("\n## 4. 原始数据（项目外，C:\\Users\\Niconiconi\\sci\\数据训练集）\n")
    md.append(md_table(
        [{"path": r["path"], "size": f"{r['size_bytes']/1024/1024:.2f} MB", "modified": r["modified"], "note": r["description"]} for r in external],
        ["path", "size", "modified", "note"],
    ))
    md.append("\n## 5. 实验日志\n")
    md.append("按批次分组；`jsonl_records` 为日志条数。旧 B 相关日志已标注 INVALID。\n")
    md.append(md_table(
        [{"path": r["path"], "records": r["jsonl_records"], "batch": r["batch"], "status": r["status"]} for r in log_rows],
        ["path", "records", "batch", "status"],
    ))
    md.append("\n## 6. 结果与指标\n")
    md.append(md_table(
        [{"path": r["path"], "description": r["description"], "batch": r["batch"], "status": r["status"]} for r in result_rows],
        ["path", "description", "batch", "status"],
    ))

    md.append("\n## 7. 审计与诚信证据\n")
    md.append(f"- 原始审计档案 `formal_experiment_archive/`：{counts['audit']} 个文件（含 archive 内部副本）。")
    md.append(f"- B 隔离证据 `B_isolation_evidence/`：{sum(1 for r in audit_rows if r['path'].startswith('B_isolation_evidence'))} 个文件。")
    md.append("- 全条件审计目录：`formal_experiment_archive/execution_record/ABCD_integrity_audit/`（含 2,000 行逐记录审计、Prompt 样例、校验和）。")
    md.append("- 原始档案包含历史端点/模型元数据，仅作审计；**不要交给 zcode**。\n")

    md.append("\n## 8. 交接材料与 2026-09-12 legacy rerun\n")
    md.append(f"- `formal_experiment_archive_for_zcode/`：{counts['handoff']} 个文件；零旧端点字迹、零密钥；当前交接版本。")
    md.append(f"- `rerun_2026-09-12/`：{counts['rerun']} 个文件；固定代码但使用 legacy 端点/模型的完整重跑。**不是 GLM 数据**，仅可作同模型交叉核验与方差来源。")
    md.append("- 该 rerun 的修正 B 方差已被合并进根目录 `results/model_variance_50samples.json`。\n")

    md.append("\n## 8b. GLM run bundle（glm_run_2026-09-12）\n")
    md.append(f"- 文件数：{counts['glm_run_bundle']}；包含已有 GLM/AIR run、项目快照、干净交接副本与原始数据副本。")
    md.append("- 已有 GLM run：`logs/glm_run_condition_{A..D}.jsonl` 各 400 条；模型 `glm-4.5-air`；Base URL `https://open.bigmodel.cn/api/paas/v4/`。")
    md.append("- 注意：`logs/glm_air_run_condition_*` 含 404/402 条重复记录，需按 TransactionID 去重；`logs/run_summary.json` 只反映最后一次 resume（24 次调用），不是完整 4,000 次审计。")
    md.append("- 归拢说明：`glm_run_2026-09-12/CONSOLIDATION_README.md`；逐文件清单：`glm_run_2026-09-12/CONSOLIDATION_MANIFEST.csv`。")
    md.append("- 安全：根目录 `config.json` 可能含真实 GLM Key，分享前必须脱敏。\n")

    md.append("\n## 9. 论文文本与说明文档\n")
    md.append("- 未发现 `.tex` 文件（搜索范围：项目根、Documents、sci、Desktop）。")
    md.append("- 说明文档（README、执行报告、审计说明、研究检索报告）汇总：\n")
    md.append(md_table(
        [{"path": r["path"], "description": r["description"], "status": r["status"]} for r in doc_rows],
        ["path", "description", "status"],
    ))

    md.append("""

## 10. 命名混乱与高风险混淆点

1. `pilot_*` 前缀现在有三种完全不同的含义，最容易误用：
   - `logs/pilot_run_condition_{A,C,D}.jsonl`：2026-09-10 的 5 条 smoke，**不是正式数据**；
   - `logs/pilot_run_condition_B_corrected.jsonl`：修正后的 400 条正式 B（名字带 pilot，但是正式数据）；
   - `results/pilot_metrics_*`：5 条 smoke 的指标。
   **建议**：正式结果引用时一律以 `formal_run_*` / `*_corrected` 为准，并在论文中不引用 `pilot_*` smoke。

2. `logs/run_summary.json` 现在只记录修正 B-only run（1,600 次），不再是最初 4,000 次主轨审计。
   完整主轨调用审计请用 `results/formal_call_audit.csv`。

3. B 相关结果分散：
   - 旧 B 污染：`formal_metrics_summary.csv`、`formal_metrics_bootstrap.json`、`formal_pairwise_tests.csv`、`formal_predictions_detail.csv`；
   - 修正 B：`corrected_B_metrics.csv`、`corrected_B_pairwise_tests.csv`、`corrected_B_results.md`。
   目前没有一份“四条件合并且全部配对检验”的最终表，定稿前最容易在 B 上引用错版本。

4. `results/formal_execution_report.md` 同时包含旧 B 修正说明与新数字，引用时注意它顶部的更正横幅。

5. `rerun_2026-09-12/` 不是 GLM 运行：它的配置使用 legacy 端点/模型。不要把它标成 GLM 结果；GLM 需用 `formal_experiment_archive_for_zcode/` 重新执行。

6. `config.json` 含真实 API Key：不要打包、上传或交给 zcode；交接只用 `config.example.json`。

7. `formal_experiment_archive/` 与 `formal_experiment_archive_for_zcode/` 内容高度相似：
   - 前者是原始审计档案（含历史模型元数据）；
   - 后者是零旧端点字迹的交接副本。
   交 zcode 必须用后者。

8. `data/test/test_cards_400.json` 与 `rerun_2026-09-12/data/test_cards_400.json` 已验证 SHA-256 相同，是同一测试集的两个副本。

9. `glm_run_2026-09-12/logs/run_summary.json` 只记录最后一次 resume 会话（24 次调用），不代表完整 GLM 主轨调用数；`glm_air_run_condition_*` 还存在 404/402 条重复记录，统计前必须按 TransactionID 去重。

10. `glm_run_2026-09-12/config.json` 可能含真实 GLM/zcode Key；该归拢目录在脱敏前不要分享或上传。

## 11. 清理建议（仅建议，不要直接删除）

论文定稿后，可考虑压缩或移出主工作目录、减少查找干扰的内容：

- `work/`：构建/验证/一次性分析脚本；建议整体打包为 `archive_work_scripts.zip` 后移出主目录；
- `outputs/` 早期文献检索报告：归档到 `archive_research_notes/`；
- `B_isolation_evidence/`：已被 `formal_experiment_archive/execution_record/ABCD_integrity_audit/03_B_isolation_evidence/` 收录，可只保留归档副本；
- `results/*_prerefix.json/csv`：审计备份，可移入 `formal_experiment_archive/`；
- `rerun_2026-09-12/`：legacy 模型交叉核验批次，可打包归档，保留 `RERUN_REPORT.md` 与 `variance_fixed_r*_condition_B.jsonl` 即可；
- `logs/sanity_*`、`logs/pilot_run_condition_{A,C,D}.jsonl`、`work/*logs*`、`rerun_2026-09-12/logs_dryrun/`：smoke/dry-run 中间产物，可归档；
- `__pycache__/`、`rerun_2026-09-12/src/__pycache__/`：可安全重建的缓存；
- `glm_run_2026-09-12/raw_data/` 是 1.26 GB 的完整复制；若不需要自包含数据集，可改为外部引用或压缩归档；
- `glm_run_2026-09-12/` 内同时存在 `glm_run_*` 与 `glm_air_run_*` 两套日志，最终报告前应先按 TransactionID 去重并确定哪一套作为权威结果；
- 建议主目录长期保留：`src/`、`config.example.json`、`data/test/`、正式日志、修正 B 结果、`results/` 权威文件、`formal_experiment_archive/` 与 `formal_experiment_archive_for_zcode/`。
""")

    (PROJECT / "PROJECT_FILE_INDEX.md").write_text("\n".join(md), encoding="utf-8", newline="\n")
    print("index written:", PROJECT / "PROJECT_FILE_INDEX.md")
    print("inventory written:", PROJECT / "PROJECT_FILE_INVENTORY.csv")
    print("project files:", len(rows), "external raw files:", len(external))


if __name__ == "__main__":
    main()
