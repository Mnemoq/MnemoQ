---
description: Surface plan deviations as decision points before implementing them
---

# Plan Deviation Protocol

When implementing from a plan file, deviations from the stated approach must be surfaced **before** coding, not mentioned after.

## Steps

1. **Identify** — While reading the plan and exploring the codebase, note any case where the plan's stated approach conflicts with what the codebase already does, or where a better approach exists.

2. **Classify** — Sort each deviation into one of two buckets:
   - **Expensive to reverse**: dependency changes, public API surface, error handling strategy, new files/modules. **Must ask before acting.**
   - **Cheap to reverse**: variable names, internal helper structure, test organization. **Note in the implementation summary, don't block on it.**

3. **Surface** — For expensive-to-reverse deviations, present as:
   > *"Plan says X. I want to do Y because [reason]. This changes [impact]. Approve?"*
   
   Not as a multiple-choice menu without context. Give the human the information needed to make the call.

4. **Batch** — If multiple deviations exist, present them all in **one checkpoint** before coding starts, not one at a time.
