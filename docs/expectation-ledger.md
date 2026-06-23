# Expectation Ledger

Expectation Ledger is a local-first contract and violation ledger for AI agents.
It stores behavioral expectations, groups them into contracts, tracks agent runs,
and records violations with evidence.

The ledger is designed for governance and review. It can search active
expectations before a run, evaluate expectations as pass/fail/unknown, and keep
violation history tied to the run that produced it.

## GitHub Description

Local-first contract and violation ledger for AI agents, with expectation search,
run tracking, and evidence.

## When To Use It

- An agent must follow explicit behavioral or operational expectations.
- Runs need durable evidence of which expectations were checked.
- Violations should be tracked without deleting the expectation history.
- Search and diagnostics should explain why an expectation was selected.

## Relationship To Memorant

Expectation Ledger and Memorant deliberately store different things. Memorant
stores trusted claims about the world or user context. Expectation Ledger stores
rules and evidence about agent behavior. A repeated violation can become a
Memorant claim only when a trust policy or operator explicitly promotes it.
