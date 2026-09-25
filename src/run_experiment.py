# -*- coding: utf-8 -*-
"""
run_experiment.py

Run the four-condition LLM pilot on serialized transaction "cards":

    A: single generic analyst prompt (one API call)
    B: Entity/Flow/Anomaly role analyses -> Decision Maker
    C: single generic analyst prompt + four e-commerce anti-fraud rules
    D: Entity/Flow/Anomaly role analyses (each with its relevant rules) ->
       Decision Maker

Every parsed outcome and every raw response is appended to
`logs/pilot_run_condition_{A,B,C,D}.jsonl`.

The HTTP layer only uses the Python standard library and talks to any
OpenAI-compatible chat-completions endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = Path(
    os.environ.get(
        "PILOT_INPUT",
        r"C:\Users\Niconiconi\sci\数据训练集\pilot_cards_100.json",
    )
)

OUTPUT_SCHEMA_PROMPT = """Return ONLY one valid JSON object with exactly these keys:
{
  "reasoning_summary": "2-4 sentence risk reasoning strictly grounded in the card text",
  "is_fraud": 0 or 1,
  "confidence": "float between 0 and 1, your estimated probability that the transaction is fraudulent"
}

Use is_fraud=1 for fraudulent, is_fraud=0 for normal. Do not include any text outside the JSON object."""

FINAL_SYSTEM_TEMPLATE = """You are a transaction fraud risk analyst for an e-commerce payment platform. You receive a serialized transaction description card and must decide whether the transaction is fraudulent.

Analysis principles:
- Base every claim strictly on values present in the card text.
- Do not invent missing facts; if a signal is absent or marked Unknown/Not provided, state that instead of guessing.
- Distinguish "a risk indicator is present" from "conclusive proof of fraud".
- Missing or unknown fields are only one weak signal among several, not proof by themselves.
{extra}
{output_schema}"""

ROLE_DEFINITIONS: Dict[str, str] = {
    "Entity": """You are the Entity Analyst. Your job is to inspect whether the people, cards, accounts, addresses and email domains in the transaction form one consistent identity and whether anything indicates impersonation, synthetic identity, card/account misuse or delivery/refund abuse.

Focus on:
- payment card type, card network, bank ID, card country;
- purchaser / recipient email domains;
- billing region and country codes, address distance offset;
- verification / match flags (M1, M2, M4, M6) and other identity consistency signals.

Return ONE valid JSON object with exactly these keys:
{"analysis": "2-4 sentences of role-specific findings grounded in the card text",
 "risk_level": "low" or "medium" or "high",
 "notable_points": ["short bullet, ..."]}""",
    "Flow": """You are the Flow Analyst. Your job is to inspect whether the transaction follows a plausible purchase flow: a normal product, amount, payment channel and timing pattern consistent with genuine e-commerce behavior.

Focus on:
- order amount and product category;
- activity/count features (C1, C2, C14) attached to the account/card;
- time-distance features (D1, D2) and whether the observed pattern fits a genuine buyer or a scripted / automated flow;
- anything that looks like an abnormal step, channel or cadence.

Return ONE valid JSON object with exactly these keys:
{"analysis": "2-4 sentences of role-specific findings grounded in the card text",
 "risk_level": "low" or "medium" or "high",
 "notable_points": ["short bullet, ..."]}""",
    "Anomaly": """You are the Anomaly Analyst. Your job is to look for statistical and behavioural deviations that are typical of fraud: unusually large or rounded amounts, very high activity counts, very short or very long gaps, extreme unknowns, or a combination of otherwise innocent signals that is itself suspicious.

Focus on:
- amount extremity / unusual product-amount combinations;
- C1/C2/C14 counts that indicate bursty or abnormal usage;
- D1/D2 time gaps that contradict a natural buying rhythm;
- environment unknowns, high-risk indicators and inconsistent match flags (M1, M2, M4, M6);
- cumulative evidence: whether several weak indicators together become a strong one.

Return ONE valid JSON object with exactly these keys:
{"analysis": "2-4 sentences of role-specific findings grounded in the card text",
 "risk_level": "low" or "medium" or "high",
 "notable_points": ["short bullet, ..."]}""",
}

ROLE_OUTPUT_PROMPT = """
Return ONLY one valid JSON object. Do not include any text outside the JSON object."""

# ================= 实验协议正式锁定的 4 条电商反欺诈规则 =================
RULES: Dict[str, str] = {
    "rule_1_email_anonymity": (
        "Email Anonymity & Absence Rule: Missing purchaser email domains ('Not provided') "
        "or domains associated with anonymous/throwaway services (e.g., 'anonymous.com') "
        "strongly indicate fraudulent identity obfuscation."
    ),
    "rule_2_frequency_burst": (
        "Velocity & Frequency Burst Rule: Abnormal spikes in recent operational counts "
        "(e.g., C2 significantly greater than 1, such as C2 >= 5, or elevated C1/C14) "
        "strongly signal automated bot testing, credential stuffing, or rapid-fire card draining."
    ),
    "rule_3_category_crossborder": (
        "High-Risk Category & Cross-Border Rule: Product category 'C' (Communication / Virtual Services) "
        "involving non-domestic card country codes (card3 != addr2 or international cards) "
        "represents a high-risk vector for irreversible laundering."
    ),
    "rule_4_distance_mismatch": (
        "Geographical & Identity Inconsistency Rule: Excessive geographic distance offsets "
        "(e.g., dist1 >= 100 miles) or verification mismatch flags (M1, M2, or M6 marked as 'F') "
        "strongly correlate with proxy IP usage and stolen credit credentials."
    ),
}
# =========================================================================

ALL_RULE_IDS: List[str] = [
    "rule_1_email_anonymity",
    "rule_2_frequency_burst",
    "rule_3_category_crossborder",
    "rule_4_distance_mismatch",
]

# 规则与 B/D 组三角色的对应映射关系
# (key 必须与 ROLE_DEFINITIONS 的角色名一致，value 为 RULES 的规则 id)
ROLE_RULE_MAP: Dict[str, List[str]] = {
    "Entity": ["rule_1_email_anonymity"],
    "Flow": ["rule_3_category_crossborder"],
    "Anomaly": ["rule_2_frequency_burst", "rule_4_distance_mismatch"],
}


class FatalAPIError(RuntimeError):
    """Non-retryable API error: bad key, insufficient balance, invalid request."""


class RunAborted(RuntimeError):
    """Raised to stop the whole run while preserving partial progress."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def render_rules(rule_ids: Sequence[str]) -> str:
    if not rule_ids:
        return ""
    lines = ["Anti-fraud rules to consider (apply each rule only when the card text contains evidence for it):"]
    for rid in rule_ids:
        lines.append("- " + RULES[rid])
    return "\n".join(lines)


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Best-effort extraction of one JSON object from an LLM response."""
    if not text:
        return None
    text = text.strip()
    # Remove a ```json ... ``` or ``` ... ``` fence if present.
    m = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if m:
        text = m.group(1).strip()

    # 1) Whole string is valid JSON.
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # 2) Slice from first '{' to last '}'.
    if "{" in text and "}" in text:
        candidate = text[text.find("{"): text.rfind("}") + 1]
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

    # 3) Balanced-brace scan as a final fallback.
    starts = [i for i, ch in enumerate(text) if ch == "{"]
    for start in starts:
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        obj = json.loads(candidate)
                        if isinstance(obj, dict):
                            return obj
                    except Exception:
                        break
    return None


def normalize_label(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        if value in (0, 1):
            return int(value)
        return None
    if isinstance(value, str):
        s = value.strip().lower()
        mapping = {
            "1": 1, "0": 0, "true": 1, "false": 0,
            "yes": 1, "no": 0, "fraud": 1, "fraudulent": 1,
            "normal": 0, "not fraud": 0, "benign": 0,
            "positive": 1, "negative": 0,
        }
        return mapping.get(s)
    return None


def normalize_confidence(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        f = float(value)
    except (TypeError, ValueError):
        s = str(value).strip().lower()
        if s in {"high", "very high"}:
            return 0.9
        if s == "medium":
            return 0.5
        if s in {"low", "very low"}:
            return 0.1
        return None
    # Accept 0-100 style scores and clamp to [0,1].
    if f > 1.0:
        f = f / 100.0
    return max(0.0, min(1.0, f))


def parse_final_response(content: str) -> Dict[str, Any]:
    obj = extract_json_object(content)
    if obj is None:
        raise ValueError("no JSON object found in model output")
    is_fraud = normalize_label(obj.get("is_fraud"))
    confidence = normalize_confidence(obj.get("confidence"))
    reasoning = obj.get("reasoning_summary")
    if not isinstance(reasoning, str):
        reasoning = json.dumps(reasoning, ensure_ascii=False) if reasoning is not None else ""
    if is_fraud is None:
        raise ValueError(f"is_fraud could not be parsed from: {obj.get('is_fraud')!r}")
    if confidence is None:
        raise ValueError(f"confidence could not be parsed from: {obj.get('confidence')!r}")
    return {
        "reasoning_summary": reasoning.strip(),
        "is_fraud": is_fraud,
        "confidence": confidence,
    }


def parse_role_response(content: str) -> Dict[str, Any]:
    """Parse an intermediate role response; callers fall back to raw text."""
    obj = extract_json_object(content)
    if obj is None:
        raise ValueError("no JSON object found in role output")
    analysis = obj.get("analysis")
    if not isinstance(analysis, str):
        analysis = json.dumps(analysis, ensure_ascii=False) if analysis is not None else content
    return {
        "analysis": analysis.strip(),
        "risk_level": str(obj.get("risk_level", "unknown")),
        "notable_points": obj.get("notable_points", []),
    }


def content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", item)))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def chat_completion_once(
    cfg: Dict[str, Any],
    messages: Sequence[Dict[str, str]],
    logger: logging.Logger,
    extra_body: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    body = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": cfg["temperature"],
        "max_tokens": cfg["max_tokens"],
    }
    if cfg.get("json_mode"):
        body["response_format"] = {"type": "json_object"}
    if extra_body:
        body.update(extra_body)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    api_key = cfg.get("api_key") or ""
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout_seconds"]) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        snippet = ""
        try:
            snippet = exc.read().decode("utf-8", errors="ignore")[:500]
        except Exception:
            pass
        message = f"HTTP {exc.code}: {snippet}"
        # 400/401/402/403/404/422 will not succeed on retry (bad request,
        # bad key, insufficient balance, forbidden, missing model, bad params).
        if exc.code in {400, 401, 402, 403, 404, 422}:
            raise FatalAPIError(message) from exc
        raise RuntimeError(message) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"network error: {exc.reason}") from exc

    if "choices" not in payload or not payload["choices"]:
        raise RuntimeError(f"unexpected response payload: {str(payload)[:500]}")
    content = content_to_text(payload["choices"][0].get("message", {}).get("content"))
    if not content:
        raise RuntimeError("empty content in model response")
    usage = payload.get("usage")
    return content, usage


def call_with_retry(
    cfg: Dict[str, Any],
    messages: Sequence[Dict[str, str]],
    logger: logging.Logger,
    dry_ctx: Optional[Dict[str, Any]] = None,
    extra_body: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[Dict[str, Any]], int]:
    """Return (content, usage, attempts). Retry transient failures."""
    retries = int(cfg["retries"])
    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            if cfg.get("dry_run"):
                content, usage = dry_chat_completion(cfg, messages, dry_ctx)
            else:
                content, usage = chat_completion_once(
                    cfg, messages, logger, extra_body=extra_body
                )
            return content, usage, attempt
        except Exception as exc:  # noqa: BLE001 - deliberate retry boundary
            last_error = exc
            if isinstance(exc, FatalAPIError):
                # Do not burn retries (or API calls) on a balance/auth problem.
                raise
            if attempt < retries:
                sleep_sec = min(2.0 ** (attempt - 1) * 2.0, 30.0)
                logger.warning(
                    "attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt, retries, exc, sleep_sec,
                )
                time.sleep(sleep_sec)
    raise RuntimeError(f"API call failed after {retries} attempts: {last_error}")


def dry_chat_completion(
    cfg: Dict[str, Any],
    messages: Sequence[Dict[str, str]],
    ctx: Optional[Dict[str, Any]],
) -> Tuple[str, Dict[str, Any]]:
    """Deterministic fake response so the pipeline can be smoke-tested offline."""
    ctx = ctx or {}
    tid = ctx.get("transaction_id", "unknown")
    condition = ctx.get("condition", "?")
    role = ctx.get("role")
    label = ctx.get("ground_truth_label", 0)
    seed = hashlib.sha256(f"{tid}|{condition}|{role}".encode("utf-8")).hexdigest()
    h = int(seed[:8], 16)
    jitter = ((h % 1000) - 500) / 10000.0  # -0.05 .. +0.05
    confidence = max(0.0, min(1.0, (0.92 if label == 1 else 0.08) + jitter))
    is_fraud = 1 if confidence >= 0.5 else 0

    usage = {
        "prompt_tokens": sum(len(m.get("content", "")) // 4 for m in messages),
        "completion_tokens": 128,
        "total_tokens": sum(len(m.get("content", "")) // 4 for m in messages) + 128,
    }

    if role:
        level = "high" if is_fraud else "low"
        payload = {
            "analysis": f"{role} analyst dry-run review of card {tid}: observed signals "
                        f"{'support' if is_fraud else 'do not support'} a fraud verdict.",
            "risk_level": level,
            "notable_points": ["dry-run placeholder point"],
        }
    else:
        payload = {
            "reasoning_summary": f"Dry-run summary for condition {condition}, card {tid}: no real API was called.",
            "is_fraud": is_fraud,
            "confidence": round(confidence, 4),
        }
    return json.dumps(payload, ensure_ascii=False), usage


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

def build_final_system(extra: str = "") -> str:
    return FINAL_SYSTEM_TEMPLATE.format(extra=extra, output_schema=OUTPUT_SCHEMA_PROMPT)


def build_a_or_c_messages(card_text: str, rule_ids: Optional[Sequence[str]]) -> List[Dict[str, str]]:
    extra = ""
    if rule_ids:
        extra = "\n" + render_rules(rule_ids)
    system = build_final_system(extra=extra)
    user = (
        "[Transaction Description Card]\n"
        f"{card_text}\n\n"
        "Analyze this transaction and decide whether it is fraudulent."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_role_messages(
    role: str,
    card_text: str,
    rule_ids: Optional[Sequence[str]],
) -> List[Dict[str, str]]:
    extra = ""
    if rule_ids:
        extra = "\n" + render_rules(rule_ids)
    system = ROLE_DEFINITIONS[role] + extra + ROLE_OUTPUT_PROMPT
    user = (
        "[Transaction Description Card]\n"
        f"{card_text}\n\n"
        "Produce your role-specific analysis."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_decision_messages(
    card_text: str,
    role_outputs: Sequence[Dict[str, Any]],
) -> List[Dict[str, str]]:
    intro = (
        "You receive the findings of three specialized analysts. Synthesize their "
        "independent evidence into one final fraud judgment. Do not repeat their full "
        "analysis in the final JSON; write a concise synthesized reasoning instead."
    )
    blocks = [intro]
    for ro in role_outputs:
        analysis_text = ro.get("analysis")
        if not analysis_text:
            analysis_text = ro.get("error") or "(role produced no usable output)"
        blocks.append(
            f"### {ro['role']} Analyst (status: {ro.get('status', 'unknown')})\n"
            f"{analysis_text}"
        )
    user = (
        "[Transaction Description Card]\n"
        f"{card_text}\n\n"
        + "\n\n".join(blocks)
        + "\n\nDecide whether this transaction is fraudulent."
    )
    return [
        {"role": "system", "content": build_final_system()},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# Condition runners
# ---------------------------------------------------------------------------

def new_sample_record(condition: str, sample: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "condition": condition,
        "transaction_id": str(sample.get("TransactionID", "")),
        "ground_truth_label": sample.get("ground_truth_label"),
        "timestamp": utc_now_iso(),
        "status": "pending",
        "reasoning_summary": None,
        "is_fraud": None,
        "confidence": None,
        "raw_response": None,
        "roles": None,
        "attempts": None,
        "api_calls": None,
        "latency_ms": None,
        "usage": None,
        "error": None,
        "fatal": False,
    }


def _finish_timing(record: Dict[str, Any], start: float, attempts: int) -> None:
    record["latency_ms"] = int((time.monotonic() - start) * 1000)
    record["attempts"] = attempts
    record["api_calls"] = attempts


def _call_one_role(
    cfg: Dict[str, Any],
    logger: logging.Logger,
    role: str,
    card_text: str,
    rule_ids: Sequence[str],
    dry_ctx: Dict[str, Any],
) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "role": role,
        "rule_ids": list(rule_ids),
        "status": "pending",
        "raw_response": None,
        "analysis": None,
        "risk_level": None,
        "notable_points": None,
        "attempts": None,
        "api_calls": None,
        "latency_ms": None,
        "usage": None,
        "error": None,
        "fatal": False,
    }
    start = time.monotonic()
    try:
        messages = build_role_messages(role, card_text, rule_ids)
        content, usage, attempts = call_with_retry(
            cfg, messages, logger,
            dry_ctx={**dry_ctx, "role": role},
        )
        record["raw_response"] = content
        record["usage"] = usage
        record["attempts"] = attempts
        record["api_calls"] = attempts
        try:
            parsed = parse_role_response(content)
            record.update(parsed)
            record["status"] = "success"
        except Exception as exc:
            # Keep the raw text as analysis so the Decision Maker can still run.
            record["analysis"] = content.strip()
            record["status"] = "parse_error"
            record["error"] = str(exc)
    except FatalAPIError as exc:
        record["status"] = "fatal_api_error"
        record["error"] = str(exc)
        record["fatal"] = True
    except Exception as exc:
        record["status"] = "api_error"
        record["error"] = str(exc)
    record["latency_ms"] = int((time.monotonic() - start) * 1000)
    return record


def _run_roles(
    cfg: Dict[str, Any],
    logger: logging.Logger,
    card_text: str,
    condition: str,
    dry_ctx: Dict[str, Any],
) -> List[Dict[str, Any]]:
    specs = []
    for role in ("Entity", "Flow", "Anomaly"):
        if condition == "D":
            rule_ids = ROLE_RULE_MAP[role]
        else:
            rule_ids = []
        specs.append((role, rule_ids))

    for role, rule_ids in specs:
        logger.info(
            "Condition %s | Role %s | Rules %s",
            condition,
            role,
            rule_ids,
        )
    max_workers = max(1, int(cfg.get("concurrency", 1)))
    if max_workers <= 1:
        return [
            _call_one_role(cfg, logger, role, card_text, rules, dry_ctx)
            for role, rules in specs
        ]
    with ThreadPoolExecutor(max_workers=min(max_workers, len(specs))) as pool:
        futures = [
            pool.submit(_call_one_role, cfg, logger, role, card_text, rules, dry_ctx)
            for role, rules in specs
        ]
        results = [f.result() for f in futures]
    return results


def run_single_prompt_condition(
    cfg: Dict[str, Any],
    logger: logging.Logger,
    condition: str,
    sample: Dict[str, Any],
) -> Dict[str, Any]:
    record = new_sample_record(condition, sample)
    start = time.monotonic()
    card_text = sample["card_text"]
    dry_ctx = {
        "condition": condition,
        "transaction_id": str(sample["TransactionID"]),
        "ground_truth_label": sample.get("ground_truth_label"),
    }
    rule_ids = ALL_RULE_IDS if condition == "C" else None
    try:
        messages = build_a_or_c_messages(card_text, rule_ids)
        content, usage, attempts = call_with_retry(cfg, messages, logger, dry_ctx)
        record["raw_response"] = content
        record["usage"] = usage
        record["attempts"] = attempts
        try:
            parsed = parse_final_response(content)
            record.update({
                "status": "success",
                "reasoning_summary": parsed["reasoning_summary"],
                "is_fraud": parsed["is_fraud"],
                "confidence": parsed["confidence"],
            })
        except Exception as exc:
            record["status"] = "parse_error"
            record["error"] = str(exc)
    except FatalAPIError as exc:
        record["status"] = "fatal_api_error"
        record["error"] = str(exc)
        record["fatal"] = True
    except Exception as exc:
        record["status"] = "api_error"
        record["error"] = str(exc)
    _finish_timing(record, start, record.get("attempts") or 0)
    return record


def run_role_condition(
    cfg: Dict[str, Any],
    logger: logging.Logger,
    condition: str,
    sample: Dict[str, Any],
) -> Dict[str, Any]:
    record = new_sample_record(condition, sample)
    start = time.monotonic()
    card_text = sample["card_text"]
    dry_ctx = {
        "condition": condition,
        "transaction_id": str(sample["TransactionID"]),
        "ground_truth_label": sample.get("ground_truth_label"),
    }
    role_outputs = _run_roles(cfg, logger, card_text, condition, dry_ctx)
    record["roles"] = role_outputs
    total_attempts = sum(int(r.get("attempts") or 0) for r in role_outputs)

    fatal_roles = [r for r in role_outputs if r.get("fatal")]
    if fatal_roles:
        record["status"] = "fatal_api_error"
        record["fatal"] = True
        record["error"] = "fatal role API error: " + ", ".join(
            f"{r['role']} ({r['error']})" for r in fatal_roles
        )
        _finish_timing(record, start, total_attempts)
        return record

    failed_roles = [r for r in role_outputs if r["status"] == "api_error"]
    if failed_roles:
        record["status"] = "api_error"
        record["error"] = "role API call failed: " + ", ".join(
            f"{r['role']} ({r['error']})" for r in failed_roles
        )
        _finish_timing(record, start, total_attempts)
        return record

    # Feed whatever the roles produced (raw text fallback included) to the DM.
    try:
        messages = build_decision_messages(card_text, role_outputs)
        content, usage, dm_attempts = call_with_retry(
            cfg, messages, logger, dry_ctx={**dry_ctx, "role": None}
        )
        record["raw_response"] = content
        record["usage"] = usage
        total_attempts += dm_attempts
        try:
            parsed = parse_final_response(content)
            record.update({
                "status": "success",
                "reasoning_summary": parsed["reasoning_summary"],
                "is_fraud": parsed["is_fraud"],
                "confidence": parsed["confidence"],
            })
        except Exception as exc:
            record["status"] = "parse_error"
            record["error"] = str(exc)
    except FatalAPIError as exc:
        record["status"] = "fatal_api_error"
        record["error"] = str(exc)
        record["fatal"] = True
    except Exception as exc:
        record["status"] = "api_error"
        record["error"] = str(exc)
    _finish_timing(record, start, total_attempts)
    return record


CONDITION_RUNNERS = {
    "A": run_single_prompt_condition,
    "C": run_single_prompt_condition,
    "B": run_role_condition,
    "D": run_role_condition,
}

# Nominal number of API calls per sample for each condition:
# A/C: 1 single analyst call; B/D: 3 role calls + 1 Decision Maker call.
CALLS_PER_SAMPLE: Dict[str, int] = {"A": 1, "B": 4, "C": 1, "D": 4}


# ---------------------------------------------------------------------------
# IO / config / CLI
# ---------------------------------------------------------------------------

def load_samples(path: Path) -> List[Dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"input file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        for key in ("samples", "data", "records"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
    if not isinstance(data, list):
        raise ValueError("input JSON must be a list of sample objects")
    for i, sample in enumerate(data):
        missing = [
            k for k in ("TransactionID", "ground_truth_label", "card_text")
            if k not in sample
        ]
        if missing:
            raise ValueError(f"sample {i} missing keys: {missing}")
    return data


def setup_logger(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("pilot")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the 4-condition LLM pilot (A/B/C/D) and write JSONL logs."
    )
    parser.add_argument("--config", type=Path, default=None,
                        help="optional JSON config file (keys under 'api' + top-level experiment keys)")
    parser.add_argument("--input", type=Path, default=None,
                        help="path to pilot_cards_100.json")
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--log-prefix", default=None,
                        help="log filename prefix, e.g. formal_run -> formal_run_condition_A.jsonl")
    parser.add_argument("--resume", action="store_true",
                        help="skip samples already logged with status=success")
    parser.add_argument("--force-resume", action="store_true",
                        help="allow resume even if the input file hash changed")
    parser.add_argument("--base-url", default=None,
                        help="OpenAI-compatible base URL, e.g. https://api.openai.com/v1")
    parser.add_argument("--api-key", default=None, help="API key (or set LLM_API_KEY)")
    parser.add_argument("--model", default=None, help="model name (or set LLM_MODEL)")
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=None, dest="timeout_seconds")
    parser.add_argument("--retries", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=None,
                        help="run B/D role calls in parallel when >1 (max useful value 3)")
    parser.add_argument("--sample-concurrency", type=int, default=None,
                        help="run multiple samples in parallel within a condition")
    parser.add_argument("--conditions", default=None,
                        help="comma-separated subset, e.g. A,C")
    parser.add_argument("--limit", type=int, default=None,
                        help="optional: only run the first N samples (for smoke tests)")
    parser.add_argument("--dry-run", action="store_true",
                        help="use deterministic fake responses; no API is called")
    parser.add_argument("--json-mode", action="store_true", dest="json_mode",
                        default=None,
                        help="send response_format={'type':'json_object'} (OpenAI-compatible endpoints)")
    parser.add_argument("--no-json-mode", action="store_false", dest="json_mode",
                        help="disable response_format JSON mode")
    return parser.parse_args(argv)


def read_json_config(path: Optional[Path]) -> Dict[str, Any]:
    if not path:
        return {}
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise ValueError("config file must contain a JSON object")
    return cfg


def build_cfg(args: argparse.Namespace) -> Dict[str, Any]:
    file_cfg = read_json_config(args.config)
    api_cfg = file_cfg.get("api", {}) if isinstance(file_cfg.get("api"), dict) else {}
    file_cfg = {**file_cfg, **api_cfg}

    def pick(cli_value: Any, key: str, env_name: Optional[str], default: Any) -> Any:
        if cli_value is not None:
            return cli_value
        if key in file_cfg and file_cfg[key] not in (None, ""):
            return file_cfg[key]
        if env_name and os.environ.get(env_name):
            return os.environ[env_name]
        return default

    cfg = {
        "input_path": Path(pick(args.input, "input_path", "PILOT_INPUT", DEFAULT_INPUT)),
        "log_dir": Path(pick(args.log_dir, "log_dir", None, PROJECT_ROOT / "logs")),
        "base_url": str(pick(args.base_url, "base_url", "LLM_BASE_URL",
                             "https://api.openai.com/v1")),
        "api_key": str(pick(args.api_key, "api_key", "LLM_API_KEY", "")),
        "model": str(pick(args.model, "model", "LLM_MODEL", "")),
        "temperature": float(pick(args.temperature, "temperature", None, 0.1)),
        "max_tokens": int(pick(args.max_tokens, "max_tokens", None, 1600)),
        "timeout_seconds": int(pick(args.timeout_seconds, "timeout_seconds", None, 120)),
        "retries": int(pick(args.retries, "retries", None, 3)),
        "concurrency": int(pick(args.concurrency, "concurrency", None, 1)),
        "sample_concurrency": int(
            pick(args.sample_concurrency, "sample_concurrency", None, 1)
        ),
        "dry_run": bool(args.dry_run or file_cfg.get("dry_run", False)),
        "json_mode": bool(pick(args.json_mode, "json_mode", None, False)),
        "log_prefix": str(pick(args.log_prefix, "log_prefix", None, "pilot_run")),
        "resume": bool(args.resume or file_cfg.get("resume", False)),
        "force_resume": bool(args.force_resume or file_cfg.get("force_resume", False)),
    }
    conditions_arg = args.conditions or str(file_cfg.get("conditions", "A,B,C,D"))
    cfg["conditions"] = [
        c.strip().upper() for c in str(conditions_arg).split(",") if c.strip().upper()
    ]
    for c in cfg["conditions"]:
        if c not in CONDITION_RUNNERS:
            raise ValueError(f"unknown condition: {c}")
    if args.limit is not None:
        cfg["limit"] = int(args.limit)
    elif "limit" in file_cfg:
        cfg["limit"] = int(file_cfg["limit"])
    else:
        cfg["limit"] = None
    if not cfg["dry_run"] and not cfg["model"]:
        raise ValueError(
            "model name is required: pass --model / set LLM_MODEL / set 'model' in config"
        )
    return cfg


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl_records(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                print(
                    f"warning: skipping corrupt JSONL line {line_no} in {path}",
                    file=sys.stderr,
                )
    return records


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def completed_ids_from_log(path: Path) -> set:
    return {
        str(r.get("transaction_id"))
        for r in read_jsonl_records(path)
        if r.get("status") == "success" and r.get("transaction_id") is not None
    }


def check_or_write_manifest(
    cfg: Dict[str, Any],
    n_samples: int,
    logger: logging.Logger,
) -> Dict[str, Any]:
    manifest_path = Path(cfg["log_dir"]) / "run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    digest = sha256_file(Path(cfg["input_path"]))
    old: Dict[str, Any] = {}
    if manifest_path.exists():
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            old = {}
    if (
        cfg.get("resume")
        and old.get("input_sha256")
        and old["input_sha256"] != digest
        and not cfg.get("force_resume")
    ):
        raise RunAborted(
            "input file hash differs from the previous run "
            f"({old.get('input_path')} -> {cfg['input_path']}); "
            "use --force-resume only if you are sure the change is intentional"
        )
    manifest = {
        "input_path": str(cfg["input_path"]),
        "input_sha256": digest,
        "samples": n_samples,
        "log_prefix": cfg["log_prefix"],
        "first_started_at": old.get("first_started_at") or utc_now_iso(),
        "last_run_at": utc_now_iso(),
        "resume": bool(cfg.get("resume")),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("input manifest | sha256=%s | file=%s",
                digest[:16], cfg["input_path"])
    return manifest


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logger = setup_logger()
    try:
        cfg = build_cfg(args)
        samples = load_samples(cfg["input_path"])
        if cfg["limit"]:
            samples = samples[: cfg["limit"]]
    except Exception as exc:
        logger.error("startup failed: %s", exc)
        return 2

    mode = "DRY-RUN (no API)" if cfg["dry_run"] else "live API"
    logger.info("pilot start | mode=%s | samples=%d | conditions=%s",
                mode, len(samples), ",".join(cfg["conditions"]))
    if not cfg["dry_run"]:
        logger.info("base_url=%s | model=%s | key_set=%s",
                    cfg["base_url"], cfg["model"], bool(cfg["api_key"]))

    expected_by_condition = {
        c: len(samples) * CALLS_PER_SAMPLE[c] for c in cfg["conditions"]
    }
    logger.info("nominal API calls per condition: %s | TOTAL=%d",
                expected_by_condition, sum(expected_by_condition.values()))

    try:
        check_or_write_manifest(cfg, len(samples), logger)
    except Exception as exc:
        logger.error("manifest check failed: %s", exc)
        return 2

    run_summary: Dict[str, Any] = {
        "started_at": utc_now_iso(),
        "mode": mode,
        "input_path": str(cfg["input_path"]),
        "input_sha256": sha256_file(Path(cfg["input_path"])),
        "samples": len(samples),
        "conditions": {},
        "expected_calls_total": sum(expected_by_condition.values()),
        "actual_calls_total": 0,
        "calls_skipped_due_to_resume": 0,
        "resume": bool(cfg.get("resume")),
        "log_prefix": cfg["log_prefix"],
    }

    summary_path = cfg["log_dir"] / "run_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    abort_reason: Optional[str] = None

    def write_summary() -> None:
        run_summary["finished_at"] = utc_now_iso()
        summary_path.write_text(
            json.dumps(run_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    try:
        for condition in cfg["conditions"]:
            log_path = cfg["log_dir"] / f"{cfg['log_prefix']}_condition_{condition}.jsonl"
            log_path.parent.mkdir(parents=True, exist_ok=True)

            completed: set = set()
            if cfg.get("resume"):
                completed = completed_ids_from_log(log_path)
            else:
                with log_path.open("w", encoding="utf-8"):
                    pass  # fresh run: truncate this condition's log

            pending = [
                s for s in samples
                if str(s.get("TransactionID")) not in completed
            ]
            per_condition: Dict[str, Any] = {
                "samples_total": len(samples),
                "completed_before_this_session": len(completed),
                "pending_this_session": len(pending),
                "expected_calls_all_samples": expected_by_condition[condition],
                "expected_calls_this_session": len(pending) * CALLS_PER_SAMPLE[condition],
                "actual_calls": 0,
                "status_counts": {},
                "elapsed_seconds": 0.0,
            }
            run_summary["conditions"][condition] = per_condition
            run_summary["calls_skipped_due_to_resume"] += (
                len(completed) * CALLS_PER_SAMPLE[condition]
            )

            if not pending:
                logger.info(
                    "condition %s already complete (%d/%d); skipping",
                    condition, len(completed), len(samples),
                )
                continue

            logger.info(
                "running condition %s | pending=%d | already_done=%d | %s",
                condition, len(pending), len(completed), log_path,
            )
            status_counter: Dict[str, int] = {}
            actual_calls = 0
            start_total = time.monotonic()
            fatal_error: Optional[str] = None

            def process_record(
                record: Dict[str, Any],
                idx: int,
                total: int,
                sample: Dict[str, Any],
            ) -> bool:
                nonlocal actual_calls, fatal_error
                status_counter[record["status"]] = (
                    status_counter.get(record["status"], 0) + 1
                )
                actual_calls += int(record.get("api_calls") or 0)
                append_jsonl(log_path, record)
                if idx == 1 or idx % 10 == 0 or idx == total:
                    logger.info(
                        "condition %s progress %d/%d", condition, idx, total,
                    )
                if record.get("fatal") and fatal_error is None:
                    fatal_error = (
                        f"condition {condition} aborted at sample "
                        f"{sample.get('TransactionID')}: {record.get('error')}"
                    )
                    return True
                return False

            try:
                sample_workers = max(1, int(cfg.get("sample_concurrency", 1)))
                runner = CONDITION_RUNNERS[condition]
                if sample_workers <= 1:
                    for idx, sample in enumerate(pending, start=1):
                        record = runner(cfg, logger, condition, sample)
                        if process_record(record, idx, len(pending), sample):
                            break
                else:
                    with ThreadPoolExecutor(max_workers=sample_workers) as pool:
                        future_map = {
                            pool.submit(runner, cfg, logger, condition, sample): sample
                            for sample in pending
                        }
                        for idx, future in enumerate(as_completed(future_map), start=1):
                            sample = future_map[future]
                            if future.cancelled():
                                continue
                            try:
                                record = future.result()
                            except CancelledError:
                                continue
                            except Exception as exc:
                                record = {
                                    "condition": condition,
                                    "transaction_id": str(sample.get("TransactionID")),
                                    "ground_truth_label": sample.get("ground_truth_label"),
                                    "status": "api_error",
                                    "api_calls": 0,
                                    "error": str(exc),
                                    "fatal": False,
                                }
                            if process_record(record, idx, len(pending), sample):
                                for pending_future in future_map:
                                    pending_future.cancel()
            finally:
                per_condition["status_counts"] = status_counter
                per_condition["actual_calls"] = actual_calls
                per_condition["elapsed_seconds"] = round(
                    time.monotonic() - start_total, 2
                )
                run_summary["actual_calls_total"] += actual_calls
            if fatal_error:
                raise RunAborted(fatal_error)
            logger.info(
                "condition %s done | status=%s | calls=%d/%d | elapsed=%.1fs",
                condition, status_counter, actual_calls,
                per_condition["expected_calls_this_session"],
                per_condition["elapsed_seconds"],
            )
    except KeyboardInterrupt:
        abort_reason = "keyboard_interrupt"
    except RunAborted as exc:
        abort_reason = str(exc)

    run_summary["aborted"] = abort_reason
    write_summary()

    logger.info("API call audit | expected=%d | actual=%d | summary=%s",
                run_summary["expected_calls_total"],
                run_summary["actual_calls_total"], summary_path)
    if abort_reason:
        logger.error("RUN ABORTED: %s", abort_reason)
        logger.error(
            "partial progress preserved. Top up / fix the issue, then rerun "
            "with --resume (and the same --log-prefix) to continue."
        )
        return 3
    logger.info("pilot finished. Logs are in %s", cfg["log_dir"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
