---
name: build-agent
description: Add LLM orchestration and evidence-grounded explanations only after quantitative tools work.
---
Read `CLAUDE.md`, `docs/ARCHITECTURE.md`, and available backend tools. Add a thin agent layer for invoking existing analytical functions. If no LLM key, fail visibly or use a clearly labeled manual CLI path; never pretend a simulated LLM is real. Tool outputs must include source, date, units and assumptions and every numerical claim must be supported by an output. Never transmit raw confidential records by default. Alerts need saved rules, deduplication and explicit user opt-in.
