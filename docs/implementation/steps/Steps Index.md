# Intro

This directory contains step-by-step specifications for the processing flow.  
The flow is designed to be **left-to-right**, deterministic, and configuration-driven (composition/parameters), while keeping business meaning in code.

## Step documents

1. [01 ParseLoadAttempt](./01%20ParseLoadAttempt.md)  
2. [02 ComputeTimeKeys](./02%20ComputeTimeKeys.md)  
3. [03 IdempotencyGate](./03%20IdempotencyGate.md)  
4. [04 ComputeFeatures](./04%20ComputeFeatures.md)  
5. [05 EvaluatePolicies](./05%20EvaluatePolicies.md)  
6. [06 UpdateWindows](./06%20UpdateWindows.md)  
7. [07 FormatOutput](./07%20FormatOutput.md)  
8. [08 WriteOutput](./08%20WriteOutput.md)  

---

## Flow overview (Mermaid)

```mermaid
flowchart TD
  %% Inputs
  IN["Input Adapter
  NDJSON lines"] --> S01[01 ParseLoadAttempt]
  S01 --> S02[02 ComputeTimeKeys]
  S02 --> S03[03 IdempotencyGate]
  S03 --> S04[04 ComputeFeatures]
  S04 --> S05[05 EvaluatePolicies]
  S05 --> S06[06 UpdateWindows]
  S06 --> S07[07 FormatOutput]
  S07 --> S08[08 WriteOutput output.txt]

  %% Config sources
  CFG[(Config YAML)] -->|"parsing rules:
  amount/time formats"| S01
  CFG -->|"time semantics:
  day/week keys"| S02
  CFG -->|"idempotency mode:
  canonical-first"| S03
  CFG -->|"feature toggles:
  Monday multiplier, prime feature"| S04
  CFG -->|"policy pack + limits:
  daily/weekly/attempts + prime gate"| S05
  CFG -->|"window semantics:
  what to update + when"| S06
  CFG -->|"output format:
  ndjson/atomic replace"| S08

  %% Window state (ports)
  WREAD[("WindowReadPort 
  state snapshots")] --> S05
  S06 --> WWRITE[("WindowWritePort 
  state mutations")]

  %% Notes
  classDef cfg fill:#f6f6f6,stroke:#999,stroke-dasharray: 3 3;
  class CFG cfg;
```

## Where configuration matters

- **01 ParseLoadAttempt**: accepted input formats, normalization rules
- **02 ComputeTimeKeys**: UTC handling, day/week key semantics
- **03 IdempotencyGate**: canonical-first behavior, duplicate classification mode
- **04 ComputeFeatures**: feature toggles (Monday, Prime) + parameters
- **05 EvaluatePolicies**: policy pack selection, evaluation order, thresholds
- **06 UpdateWindows**: what windows exist and which decisions mutate them
- **08 WriteOutput**: output mode (NDJSON), file path, atomic replace/fsync
