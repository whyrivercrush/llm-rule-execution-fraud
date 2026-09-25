# Verbatim prompt templates and fingerprints

This document reproduces the frozen prompt material used across all conditions,
together with the SHA-256 digests that let a reader verify byte-identity
independently. The strings below are the text **as issued to the models**, not
abbreviations. They are byte-identical across both model tracks.

The same material appears in the Supplementary Information to the manuscript
(section "Reproducibility materials: verbatim prompt templates"); it is repeated
here so that a reader working from the repository alone has everything in one
place.

To check that the prompts you are about to send match the ones behind the
reported numbers:

```bash
python src/verify_prompt_integrity.py
```

---

## 1. How a prompt is assembled

Every condition is built from a small number of frozen constants:

| Constant | Role |
|---|---|
| `FINAL_SYSTEM_TEMPLATE` | the monolithic analyst system prompt (Conditions A and C) |
| `OUTPUT_SCHEMA_PROMPT` | the JSON output instruction |
| `RULES` | the rulebook, injected as a block in the system message |
| `ROLE_DEFINITIONS` | the three analyst personas (Conditions B and D) |
| `ROLE_RULE_MAP` | which rules each role receives (Condition D only) |

**Conditions A and C** use the monolithic template. The rules block is injected
into the *system* message, which is why the A and C user prompts share a digest.

**Conditions B and D** use the three role templates plus a decision-maker
(arbiter) prompt. In Condition D the rules are partitioned across roles and the
arbiter receives no rule text at all.

---

## 2. Shared output schema (Conditions A and C)

```
Return ONLY one valid JSON object with exactly these keys:
{
  "reasoning_summary": "2-4 sentence risk reasoning strictly grounded in the card text",
  "is_fraud": 0 or 1,
  "confidence": "float between 0 and 1, your estimated probability that the transaction is fraudulent"
}
Use is_fraud=1 for fraudulent, is_fraud=0 for normal. Do not include any text outside the JSON object.
```

---

## 3. Monolithic system template (Conditions A and C)

```
You are a transaction fraud risk analyst for an e-commerce payment platform.
You receive a serialized transaction description card and must decide whether
the transaction is fraudulent.

Analysis principles:
- Base every claim strictly on values present in the card text.
- Do not invent missing facts; if a signal is absent or marked
  Unknown/Not provided, state that instead of guessing.
- Distinguish "a risk indicator is present" from "conclusive proof of fraud".
- Missing or unknown fields are only one weak signal among several, not proof
  by themselves.

[+ rules block, Condition C only]
[+ output schema]
```

---

## 4. The rules block

The block is emitted by a single template applied to whichever rule set the
condition uses. **The preamble line is identical in every rulebook condition**,
including the sham and de-hedged controls — that is what makes the controls
comparable.

### 4.1 Real rulebook (Condition C, and C-R2only with one rule)

Preamble, then:

```
Anti-fraud rules to consider (apply each rule only when the card text contains
evidence for it):
- Rule 1. Email Anonymity & Absence Rule: Missing purchaser email domains
  ('Not provided') or domains associated with anonymous/throwaway services
  (e.g., 'anonymous.com') strongly indicate fraudulent identity obfuscation.
- Rule 2. Velocity & Frequency Burst Rule: Abnormal spikes in recent
  operational counts (e.g., C2 significantly greater than 1, such as C2 >= 5, or
  elevated C1/C14) strongly signal automated bot testing, credential stuffing, or
  rapid-fire card draining.
- Rule 3. High-Risk Category & Cross-Border Rule: Product category 'C'
  (Communication / Virtual Services) involving non-domestic card country codes
  (card3 != addr2 or international cards) represents a high-risk vector for
  irreversible laundering.
- Rule 4. Geographical & Identity Inconsistency Rule: Excessive geographic
  distance offsets (e.g., dist1 >= 100 miles) or verification mismatch flags
  (M1, M2, or M6 marked as 'F') strongly correlate with proxy IP usage and stolen
  credit credentials.
```

**C-R2only** issues exactly the Rule 2 string above, with the identical preamble
and no other rule present. The `C1/C14` disjunct visible in it is the residual
specification asymmetry discussed in the manuscript's Result 1.

### 4.2 C-crisp — the de-hedged rule set

The four rules are replaced by hard-threshold statements so that the condition
reproduces the deterministic engine's conditions exactly:

```
- Rule 1. Email Anonymity Rule: Flag if and only if the purchaser email domain
  is 'Not provided' or is exactly 'anonymous.com'.
- Rule 2. Velocity Burst Rule: Flag if and only if C2 >= 5.
- Rule 3. High-Risk Category & Cross-Border Rule: Flag if and only if the
  product category is 'C' and card3 != addr2.
- Rule 4. Geographical & Identity Inconsistency Rule: Flag if and only if
  dist1 >= 100, or if any one of M1, M2, M6 equals 'F'.
```

### 4.3 C-sham — the semantically empty rule set

These preserve the structure, length and bulleted-block presentation of the real
rulebook while carrying **no fraud semantics**:

```
- Rule 1. Identifier Parity Rule: Transaction identifiers terminating in an odd
  digit, when combined with order amounts whose integer component is even,
  strongly indicate elevated manual review priority.
- Rule 2. Digit Repetition Rule: Order amounts containing the same digit twice
  in succession (e.g., 11.00, 5.55, 33.20) strongly signal anomalous processing
  conditions requiring additional scrutiny.
- Rule 3. Round-Number Proximity Rule: Order amounts falling within ten US
  dollars below a multiple of one hundred (e.g., 89.00, 190.00) strongly
  correlate with irregular account behaviour.
- Rule 4. Domain Length Rule: Purchaser email domains whose character count is
  divisible by three strongly associate with irregular registration patterns and
  warrant additional verification.
```

---

## 5. Role system prompts (Conditions B and D)

Each sub-agent receives its role definition, then (Condition D only) its
allocated rules, then the suffix:

```
Return ONLY one valid JSON object. Do not include any text outside the JSON object.
```

with output schema:

```json
{"analysis": "...", "risk_level": "low|medium|high", "notable_points": [...]}
```

### Entity Analyst

```
You are the Entity Analyst. Your job is to inspect whether the people, cards,
accounts, addresses and email domains in the transaction form one consistent
identity and whether anything indicates impersonation, synthetic identity,
card/account misuse or delivery/refund abuse.
Focus on: payment card type, card network, bank ID, card country; purchaser /
recipient email domains; billing region and country codes, address distance
offset; verification / match flags (M1, M2, M4, M6).
```

### Flow Analyst

```
You are the Flow Analyst. Your job is to inspect whether the transaction follows
a plausible purchase flow: a normal product, amount, payment channel and timing
pattern consistent with genuine e-commerce behaviour.
Focus on: order amount and product category; activity/count features (C1, C2,
C14); time-distance features (D1, D2) and whether the observed pattern fits a
genuine buyer or a scripted / automated flow.
```

### Anomaly Analyst

```
You are the Anomaly Analyst. Your job is to look for statistical and behavioural
deviations that are typical of fraud.
Focus on: amount extremity; C1/C2/C14 counts indicating bursty usage; D1/D2 time
gaps contradicting a natural buying rhythm; environment unknowns, high-risk
indicators and inconsistent match flags; cumulative evidence.
```

---

## 6. Arbiter prompt (Conditions B and D)

System prompt: the shared monolithic template **without** any rules.

User prompt:

```
[Transaction Description Card]
<card_text>

You receive the findings of three specialized analysts. Synthesize their
independent evidence into one final fraud judgment.

### <Role> Analyst (status: <status>)
<analysis>
...

Decide whether this transaction is fraudulent.
```

---

## 7. Verified role–rule allocation (Condition D)

```
Entity  -> Rule 1
Flow    -> Rule 3
Anomaly -> Rules 2 and 4
```

**The arbiter receives no rule text in Condition D.** No single agent in
Condition D therefore observes the complete rulebook — a confound the manuscript
discusses explicitly and does not attempt to remove.

---

## 8. SHA-256 fingerprints

Digests of the exact template strings, computed with the card text replaced by a
placeholder. Verified programmatically immediately before execution and again
against the returned logs.

### Frozen constants

| Template | SHA-256 (first 16 hex) |
|---|---|
| `FINAL_SYSTEM_TEMPLATE` | `1fbfbedfb5d01a3f` |
| `OUTPUT_SCHEMA_PROMPT` | `d9ce65d2fd580c41f` |
| `ROLE_OUTPUT_PROMPT` | `e1085d364aa69044f` |
| `ROLE_DEFINITIONS_Entity` | `2e1586a299932b2c` |
| `ROLE_DEFINITIONS_Flow` | `3c7b9c423aa231646` |
| `ROLE_DEFINITIONS_Anomaly` | `7dd53d79827db6cb72` |
| `RULES_json` (frozen rulebook) | `baa6e05abec636c9` |
| `ROLE_RULE_MAP_json` | `23a766042e230e56` |

### Assembled templates

| Condition | Message | SHA-256 (first 16 hex) |
|---|---|---|
| A | system | `0324572661831aa4` |
| A | user | `1f324e96fdd25887` |
| C | system | `36f031bec8f42b7a` |
| C | user | `1f324e96fdd25887` |
| Decision template | system | `0324572661831aa4` |
| Decision template | user | `6f51e6bb2f91e94e` |
| B | Entity system | `7b0decd3eb63362c` |
| B | Flow system | `ccf32b0a88c8128c` |
| B | Anomaly system | `17e507b085a73698` |
| D | Entity system | `03680c54e414b049` |
| D | Flow system | `f5e080a9f729422f` |
| D | Anomaly system | `aea7d4f07e1adfce` |

**Notes.** All sub-agent user templates in Conditions B and D share the digest
`6fc1c6680cc64603`. The A/C pair shares a user template because the rules block
is injected in the *system* message; the A and decision-template system digests
coincide for the same reason.

---

## 9. Reusing these prompts

If you adapt this protocol, note three things that the manuscript treats as
load-bearing:

1. **The preamble is shared.** The sham and de-hedged controls use the same
   preamble line as the real rulebook. Changing it breaks the comparison.
2. **The engine implements only part of Rule 2.** The prompt text licenses a
   `C1/C14` disjunct that the deterministic engine does not evaluate. This gap
   is documented in the manuscript's specification-gap table, and C-crisp exists
   specifically to close it.
3. **Condition D's arbiter sees no rules.** This is deliberate and is a confound,
   not an oversight — see the manuscript's discussion of rule visibility.
