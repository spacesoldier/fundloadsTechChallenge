# System Architecture and Decision-Making Logic

## 1. General Overview

### 1.1 Problem Scope and Industry Context

The [[Challenge task|task]] belongs to a class of problems commonly encountered in **transaction screening and decisioning systems** within financial and regulated domains.

At its core, the problem models a simplified **real-time adjudication engine**, responsible for deciding whether an incoming operation may proceed based on:

- temporal limits (per day, per week),
- cumulative state (amounts, attempt counts),
- contextual risk factors (calendar-based or identifier-based),
- and global constraints spanning multiple actors.

Similar patterns appear in production systems such as:

- payment authorization and load velocity control,
- fraud pre-screening and risk scoring pipelines,
- regulatory compliance enforcement (e.g. AML/KYC-related controls),
- rate limiting and quota enforcement with domain-specific semantics.

Although the challenge itself is intentionally constrained, the underlying structure reflects real-world systems where:

- decisions must be made **online**, on a per-event basis,
- rules must be **auditable and explainable**,
- configuration changes must not require code redeployment,
- and the system must remain extensible as new risk signals emerge.

### 1.2 Solution overview

This solution treats the task not as an isolated coding exercise, but as a **reduced model of a policy-driven decision engine**, deliberately balancing correctness, clarity, and architectural discipline without expanding beyond the scope of the problem statement.

The system is designed for **stream processing of operation attempts** and for making accept/reject decisions based on a configurable set of policies.

The architecture follows a **pipeline model**:

`Input → Gate → Modifiers → Window Aggregation → Policy Engine → Window Aggregation → Output`

Each stage has a clearly defined responsibility and does not contain logic belonging to other stages.

### 1.3 Solution Approach (High-Level)

The solution is built around a **processing pipeline** with clear separation of responsibilities:

```
Input Stream
    ↓
Input Adapter
    ↓ 
Idempotency gate
    ↓
Modifiers / Applicators
    ↓ 
Window Aggregation Read
    ↓ 
Policy Engine
    ↓ 
Decision Output
    ↓ 
Window Aggregation Commit
    ↓
Output Adapter
    ↓
Output Stream        

```

#### Key design principles:

- **Streaming-first**: input is processed sequentially without loading the entire dataset into memory.
- **Separation of concerns**:
    - modifiers prepare and enrich data,
    - policies evaluate constraints and make decisions.
- **Configuration-driven behavior**: policy parameters are defined outside of code.
- **Deterministic and explainable decisions**: every rejection can be traced back to a specific rule.
- **Constrained scope**: the system does not attempt to become a generic rule engine.

[[Reasoning and assumptions|Detailed architectural reasoning and assumptions]]

---


## 2. Input Data Flow

### 2.1 Input Adapter

The system is not coupled to a specific data source.  
All incoming events enter the system through an **input adapter**, which:

- reads data sequentially (streaming)
- converts input into an internal representation
- forwards events one by one to the processing pipeline

Currently, a single adapter is implemented:
- **FileInputAdapter** — line-by-line file reader

The architecture allows additional adapters to be added in the future, such as:
- message queues
- HTTP endpoints
- stdin / pipes
without modifying the core processing logic.

### 2.2 Idempotency Gate

#### Goal
The input stream may contain **repeated attempts** with the same `id`.  To keep the adjudication engine deterministic and safe, the pipeline includes an **Idempotency Gate** that classifies repeated `id` occurrences and prevents duplicates from affecting velocity windows and downstream policies. Detailed reasoning could be found in [[Input data analysis - idempotency|detailed analysis]].

#### Terminology
- **Idempotency key**: `id` from the input event is treated as the unique identifier of an attempt for idempotent processing.
- **Replay**: the same `id` is received again with the **same payload**.
- **Conflict**: the same `id` is received again with a **different payload** (e.g., a different `load_amount`).

#### Payload fingerprint
To distinguish replay from conflict, the gate computes a **payload fingerprint** from a normalized subset of fields (excluding `id`):
- `customer_id`
- `time`
- `load_amount` (normalized as a numeric value with scale 2)

Fingerprint excludes `id` intentionally: replay/conflict classification must be based on whether the **request content** changed, not on the key itself.

#### Behavior
For each incoming event:
1. If `id` has not been seen:
   - the event is passed downstream for regular processing.
2. If `id` has been seen:
   - if the new fingerprint matches the stored fingerprint → **Replay**
     - the gate returns the **same decision** as for the first occurrence (idempotent behavior),
     - the event **bypasses** the window aggregator and policy engine.
   - if the fingerprint differs → **Conflict**
     - the event is **rejected deterministically** as a conflicting duplicate,
     - the event bypasses the window aggregator and policy engine.

#### State and update semantics
The gate stores (per `id`):
- `payload_fingerprint`
- the resulting `decision` (accepted/rejected + reason)

The state is written only after the first occurrence completes decisioning. This ensures:
- duplicates do not mutate window counters,
- repeated deliveries (replays) are stable and deterministic.

#### Rationale
- Streaming processing cannot “know in advance” that a first occurrence will be duplicated later. The correct approach is to process the first occurrence normally, and handle subsequent repeats via idempotency semantics.
- Conflicting duplicates are treated as unsafe input and are rejected to protect correctness (financial/regulatory context).
- Idempotency is handled **before** velocity windows and policies to avoid wasted compute and, more importantly, to prevent accidental state corruption.


---

## 3. Modifiers (Applicators)

### 3.1 Purpose

Modifiers are components that **transform or annotate the input vector** but **do not make accept/reject decisions**.

They are responsible for:

- computing derived parameters    
- adding flags or markers
- adjusting numeric values used later by policies

Modifiers are intentionally unaware of:

- limits
- counters
- decision outcomes

Their role is to prepare data for downstream stages.

---

### 3.2 Example: Calendar-Based Risk Modifier

Within the scope of this challenge, a calendar-based risk modifier is implemented.

For specific calendar conditions (e.g. Mondays):
- a multiplier is applied to the operation amount

This introduces the concept of a **counted amount** — the value used by subsequent policy checks.

The modifier:
- evaluates the timestamp
- applies the configured multiplier when the condition matches
- passes the updated vector downstream

---

### 3.3 Example: Identifier-Based Modifier

Another class of modifiers evaluates identifier properties.

For example:
- detecting whether an identifier is a prime number
- attaching a corresponding flag (e.g. `prime_id`)

Such flags do not cause rejection by themselves.  
They act as **risk markers**, which are later interpreted by specific policies.

---

## 4. Window Aggregation

### 4.1 Purpose of Windows

Many policies operate not on individual events but on **aggregated state over time**, such as:
- total amount per day
- total amount per week
- number of attempts per period

The window aggregation layer:
- groups events into time windows
- maintains counters and accumulators
- exposes these aggregates to the policy engine

---

### 4.2 Window Types

Two base window types are supported:
- `day`
- `week`

Each window can operate in one of two modes:
- **calendar** — aligned to calendar boundaries (UTC)
- **rolling** — sliding window of fixed duration

The window mode is defined in configuration and shared by all policies that reference the window.

---

## 5. Decision-Making Block (Policy Engine)

### 5.1 Purpose

The Policy Engine is the only component that:
- evaluates the current event
- considers aggregated window state
- applies configured policies
- produces a final decision: accept or reject

---

### 5.2 Policies

A policy is a rule that:
- checks a single constraint
- either allows processing to continue
- or rejects the event with a specific reason

Examples of policies include:
- daily amount limit
- weekly amount limit
- daily attempt limit
- global constraints triggered by specific flags

Policies:
- do not modify data
- are independent of input source
- operate solely on the current vector and window state

---

### 5.3 Policy Evaluation Order

Policies are applied sequentially in a fixed order.
On the first violation:
- processing stops
- the event is rejected

If all policies pass:
- the event is accepted
- window aggregates are updated accordingly

---

## 6. Control and Configuration

### 6.1 Control Block

Modifiers and policies do not embed hardcoded parameters.  
All numeric values and operating modes are supplied via a **control block**.

The control block:
- loads and validates configuration
- provides parameters to all components
- guarantees configuration consistency

---

### 6.2 Configuration Adapter

Similar to input handling, configuration is loaded through an adapter.
Currently implemented:
- **YAMLConfigAdapter**

It:
- reads configuration files    
- validates structure and allowed values
- builds an internal configuration model

The architecture allows alternative configuration sources in the future:

- databases
- remote configuration services
- environment-based configuration

---

## 7. Resulting System Model

As a result, the system provides:
- streaming processing without loading all input into memory
- strict separation of concerns:
    - input acquisition
    - parameter modification
    - aggregation
    - decision-making
- configurable behavior without code changes
- extensibility through new adapters, modifiers, and policies

At the same time, the implementation remains intentionally constrained to the scope of the challenge and does not attempt to become a generic rule engine.


# 8. Implementation

Implementation starts with [Development plan](./implementation/Development%20plan.md)





