# Matrix Auto Cutter — OUTRO-1 Architecture Addendum v1.0

## Status and scope

This addendum records the OUTRO-1 contract extension. The frozen
`matrix-auto-cutter-architecture-plan-v0.2.md` remains byte-for-byte unchanged.
It continues to define Sidecar 1.1; this addendum does not reinterpret that
contract.

## Versioned sidecar contracts

`obs_event_sidecar` **1.1** remains the original strict contract. Its public
event schema has no `scene_uuid`; unknown fields are rejected. Canonical 1.1
bytes and existing consumers therefore retain their prior meaning.

`obs_event_sidecar` **1.2** retains every 1.1 field and adds the optional
`scene_uuid` event field. It is a canonical UUIDv4, is permitted only on a
`scene_changed` event, and is omitted rather than represented as JSON `null`
when unavailable. `scene_name` remains the exact human-readable scene name.
Unknown fields remain rejected for both versions.

The finalizer writes Sidecar 1.2 for new finalizations. It maps journal
`source_uuid` to `scene_uuid` and the journal label to `scene_name` only for
`scene_changed`. Older journals without a stable scene UUID still finalize to
valid 1.2 artifacts with that field absent.

Consumers dispatch strictly by `schema_version` and accept both 1.1 and 1.2;
they never implicitly upgrade a loaded 1.1 artifact. A 1.1 artifact can still
provide the existing clock, protection, and silence-analysis evidence, but can
never authorize OUTRO-1 tail evidence. Only a fully valid 1.2 scene event with
the stable UUID can enter the explicit local outro-binding resolution.

## Proposal binding

OUTRO-1 uses Proposal 1.1. The proposal remains bound to the exact Sidecar
SHA-256. Its binding digest and typed outro-resolution evidence are included in
the proposal digest even when no tail is available. Consequently switching
between Sidecar 1.1 and 1.2, or changing the binding file, cannot reuse a prior
proposal, selection, or approval under altered evidence.
