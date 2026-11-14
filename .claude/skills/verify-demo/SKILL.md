---
name: verify-demo
description: Validate the full HackPrinceton demo without mock-success claims or invented data/model accuracy.
---
Run `pytest -q` plus both industry demos. Audit synthetic labels, exception handling, env secrets, source provenance, action feasibility, calibration claims, rolling-origin leaks, and the gap between working and planned features. Verify at least one financial and one operational use case; report real output, failures and a prioritized fix list. Do not claim live source access unless a real request succeeded with approved credentials.
