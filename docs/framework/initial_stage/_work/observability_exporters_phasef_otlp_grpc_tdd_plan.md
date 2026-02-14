# Phase F: OTLP gRPC backend (grpcio) (TDD)

## Objective

Add binary OTLP gRPC exporter backend for lower overhead and collector-native pipeline.

## Deliverables

- gRPC channel/stub exporter path for OTLP traces;
- secure/insecure channel config model;
- retry/deadline mapping for gRPC status codes.

## RED tests

- `OBS-GRPC-01` valid OTLP span batch is sent via gRPC export call.
- `OBS-GRPC-02` channel creation respects endpoint and tls/insecure mode.
- `OBS-GRPC-03` deadline/timeout settings map to call options.
- `OBS-GRPC-04` retriable status codes follow configured retry policy.
- `OBS-GRPC-05` non-retriable failure increments dropped/error counters and is isolated.

## GREEN target

- backend selectable via `backend=grpcio`;
- payload path remains trace-compatible with existing Jaeger/collector flow;
- exporter can run in both sync and async execution profiles through queue worker abstraction.

## Refactor

- isolate protobuf mapping layer behind transport-neutral span batch interface.

## Exit criteria

- gRPC backend tests green;
- collector integration smoke test passes with local OTLP gRPC endpoint.
