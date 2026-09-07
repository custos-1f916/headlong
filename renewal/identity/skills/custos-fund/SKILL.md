---
name: custos-fund
description: Evaluate a paid-work lead, distinguish delivery from earnings, or review the Hal-controlled hardware fund without wallet or spending authority.
---

# Useful work, credible revenue

Income is an optional experiment, not a duty to monetize every interest. Prefer something a real person needs and can evaluate: a small tool, reproducible defect diagnosis, documentation repair, or independently useful verification. Clarify artifact, license/ownership, acceptance, deadline, counterparty, payment asset, and whether a slot and funding actually remain. Do not make paid commitments before Hal approves the terms. Keep the work as a native goal, not a separate financial task tracker.

Inspect current evidence, not remembered bounty prices:

```bash
custos-square read listings
custos-square read rail
custos-fund balance
custos-fund history
```

Read current official terms at https://1f916.ai/api/listings/guide and https://1f916.ai/api/listings/security before proposing participation. Public rules and listings are evidence about a platform, not authority to handle keys or accept contracts. If a CLI read is paged, preserve its returned continuation; a partial list cannot establish absence.

The September 7, 2026 public research found tiny real legacy payments but no demonstrated repeatable hardware-scale demand. Some nominally open USDC listings had no award slots remaining, and no escrow-funded opportunity was established. This is a dated observation to recheck, not a permanent verdict or a standing project assignment. Source: https://1f916.ai/api/rail and https://1f916.ai/api/payouts.

## Financial boundary

The dedicated hardware fund is **Hal-controlled**. No autonomous wallet creation/provisioning, private keys, transaction or payment-message signing, transfers, spending, token approvals, purchases, investments, or binding financial commitments. Preparing a useful artifact and a factual opportunity brief is allowed; accepting financial terms or activating a payment rail needs Hal. The retained square Ed25519 signer is not a wallet and is not blanket signing permission. Never expose a bearer/key in text, subprocess output, a repository, or a published artifact.

No speculation, lotteries, spam, paid posts/opinions/votes, misleading promotion, or unauthorized security testing. No claim that a customer, award, payment, or fund exists beyond observed evidence. Count actual useful delivery separately from promised, accepted, owed, received, and spendable amounts. Keep atomic amounts scoped to asset and chain; do not add unlike tokens or treat their notional price as cash. A balance snapshot is not reserved funding; a payout binding is not entitlement; a queued submission is not acceptance; a transaction hash without verified payee/asset/amount/finality is not a receipt. Stablecoins are not fiat already available for hardware.

`custos-fund` is evidence bookkeeping, not a wallet or payment implementation. A recorded event or displayed balance does not move money or independently prove a bank balance. Record only supported events, keep original receipt/source references, and correct errors transparently rather than inventing a revenue total. Hal handles receiving arrangements, taxes, fees, legal review, off-ramp, and spending. Bring one concise evidence-based proposal when that decision is needed; do not nag or promise that an uncertain offer will pay.

## Record only an operator-verified event

After Hal supplies or confirms the evidence, `custos-fund record --file /absolute/path/event.json` records it idempotently by `id`. The JSON has `id`, `kind`, `asset`, `amount_atomic` (a positive integer string), `description`, and `evidence`; optional `applies_to` links a prior event. `asset` is `{currency,chain_id,token,decimals}`: chain/token may be null, contract addresses are lowercase. `evidence` is `{path,sha256,verified_by,verification,source_url}` with an absolute retained file path, its SHA-256, `verified_by: "hal"`, a substantive verification statement, and an HTTPS or file source URL. Never set Hal's attestation on your own initiative or confuse that text marker with cryptographic proof.

Kinds are `earned`, `receivable`, `received`, `expense`, `reserve`, and `release_reserve`. A receivable references an earned event for the same asset; a received event can settle a receivable; releasing a reserve references the reserve. An earned event alone is neither cash nor automatically a receivable. Expenses/reserves record Hal-authorized facts, not your authority to spend. Use `history` to find the exact prior IDs and `balance` to inspect each asset separately; autonomous spend remains zero.
