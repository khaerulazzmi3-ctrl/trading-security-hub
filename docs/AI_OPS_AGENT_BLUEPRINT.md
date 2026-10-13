# AI Ops Universe — Agent & Operations Blueprint

This document is the implementation contract for the prototype. It distinguishes the planned system from features already implemented.

## Core principles
- Trading and security are separate operational modules with separate views and server-side permissions.
- The UI is never an authority: all role decisions happen in the backend.
- The four access states are distinct: `blocked`, `ordinary`, `admin`, and `privileged`.
- No real-money order execution until a strategy passes historical backtest, out-of-sample (OOS) validation, and paper/demo forward testing, with an explicit user approval gate.
- Every agent task must be auditable: owner, purpose, inputs/source timestamps, status, progress, result, errors, approvals, and events.
- AI-provider spend must be explicitly budgeted. No automatic top-up; fail closed when the configured budget is exhausted.
- Never put API keys in the browser, repository, logs, task output, or GitHub Pages frontend.

## Access governance contract
- `blocked`: no authenticated application access; recovery is a request, not self-service role escalation.
- `ordinary`: common dashboard, own task history, permitted analysis and paper/demo functions.
- `admin`: owner/operator functions, account administration, Trading Ops, Security Ops and connector configuration; cannot grant the `privileged` role.
- `privileged`: separately approved governance access for privileged access decisions, incident/audit governance and permission revocation. Do not silently merge this with Admin.
- First Admin: one-time backend bootstrap using a high-entropy secret delivered through server environment, not a public dashboard button. Disable/remove the bootstrap secret after creation.
- Trusted phone/PC registration must be bound to an authenticated account and revocable server-side. IP address or browser-provided device labels are not proof of identity.
- Sensitive changes should require re-authentication or MFA/WebAuthn before public production.

## Guardian actions (four distinct flows)
1. **Periksa Keamanan** — read current session/access state, incident records, audit trail and health checks; do not claim to detect compromise beyond available telemetry.
2. **Ajukan Buka Blokir** — create a review request; cannot unblock itself.
3. **Izin Akses** — request ordinary/feature-level access and show current permission; cannot grant its own role.
4. **Akses Admin** — admin-only control surface for operational configuration and account administration. Privileged-only governance decisions remain separately protected.

Each action needs its own view, API action, permission guard, audit event and explicit result state. Blocked users may only access a narrowly scoped recovery/request route.

## Agent roster and duties
### 1. Guardian / Security Agent
- Review authentication failures, session expirations/revocations, role changes, access requests, incident events and deployment health.
- Produce evidence-linked incident summaries and severity suggestions.
- Require human approval for role changes, unblocking, secret rotation and other security-impacting actions.
- Never claim full intrusion detection without logs/telemetry sources.

### 2. Trading Operations Supervisor
- Monitor connector health, data freshness, job status, risk limits, strategy validation stage, paper PnL and error state.
- Block promotion to demo/live when required validation evidence is missing.
- Report state only; it is not itself a trading signal generator.

### 3. IDX Corporate Action Research Agent
- Ingest official IDX/company disclosures when a verified source connector is implemented.
- Normalize issuer, ticker, disclosure time, action category, effective dates, tender offer/rights issue/buyback/acquisition/ownership-change fields.
- Keep facts, market rumours, inference and technical observations in separate fields.
- Alert with source URL/time and an explicit “TA bukan rekomendasi” label where technical analysis is included.
- Do not invent corporate actions or treat rumours as confirmed disclosures.

### 4. Fundamental / Value Agent
- Parse financial reports with reporting period and source lineage.
- Calculate valuation scenarios and margin of safety; apply the user's value-investing gate of at least 30% discount to estimated intrinsic value as a research filter, not a guaranteed return.
- Expose assumptions, uncertainty, sensitivity and stale-data warnings.

### 5. Asset-Specific Technical Agents
- **IDX stocks:** price/volume, market structure, support/resistance, Fibonacci, daily timeframe and broker-summary inputs only when verified data is available; corporate actions remain a separate signal class.
- **Forex:** ICT/SMC/CRT rules and session context.
- **Commodities:** trend, volatility, supply/demand and relevant market sessions.
- **Crypto:** trend/momentum, liquidity and volume.
- Never reuse the forex ICT/SMC/CRT rule set indiscriminately for IDX stocks.
- Strategies are configuration/versioned artifacts and must pass backtest + OOS + paper/demo before promotion.

### 6. Backtest & Strategy Validation Agent
- Validate data gaps, look-ahead bias, survivorship bias, transaction costs, spread/slippage, position sizing, drawdown and sample size.
- Separate in-sample, OOS and forward paper/demo results.
- Reject invalid or incomplete test runs; preserve immutable run metadata and strategy version.
- Never guarantee future performance.

### 7. Data & Connector Health Agent
- Monitor source availability, schema changes, timestamps, missing bars, duplicate records, rate limits and reconnect status.
- Initial planned connections: MT5 terminal/Python MetaTrader5 using a demo account first; Stockbit, TRIV, and other broker/exchange/securities connectors only where documented APIs and permissions allow.
- A disconnected or stale feed must be shown as disconnected/stale, never simulated as live.

### 8. Reporting & Alert Agent
- Route status, incident and research alerts to the dashboard and optionally outbound Telegram once secrets are configured.
- Deduplicate and rate-limit alerts; include timestamp, severity, source, and why it triggered.
- Telegram is reporting-only unless a separately reviewed command interface is designed; no command execution through inbound Telegram in this foundation.

### 9. AI Provider / Budget Guardian
- Use a server-side provider adapter for Grok or another configured model.
- Enforce request/token/cost ceilings before every call; maintain usage ledger and a hard stop at budget limit.
- No automatic top-up. Do not call the provider when credentials or budget are missing.
- Keep model output untrusted: validate schemas and tool permissions, record provenance, and require approval for external side effects.

## Task lifecycle
`queued -> running -> waiting_approval -> completed | failed | cancelled`
- Tasks need unique IDs, owner scope, idempotency, bounded retries, lease/heartbeat for workers, timeouts, cancellation and event history.
- Tool actions must be allow-listed. No arbitrary shell execution, unrestricted URL fetch, or unreviewed code execution.
- Read-only analysis can be automated after source and budget controls are implemented. Changes to accounts, secrets, deployment, or orders require explicit approval.
- The current task engine is a synchronous local stub; the roster above is the intended contract, not a claim that these agents are operational.

## Delivery sequence
1. Harden access/bootstrap, test role boundaries and task ownership.
2. Connect Guardian and Agent Operations UI to authenticated backend.
3. Add durable worker/lease and explicit approval workflow.
4. Add provider adapter with budget hard-stop and redacted logs.
5. Add data/connector health before market analysis.
6. Implement official IDX disclosure ingestion and corporate-action normalization.
7. Implement valuation/technical agents and data-quality controls.
8. Build backtest/OOS/paper pipeline and risk limits.
9. Add MT5 demo connector and other connectors individually.
10. Enable real execution only after independent review and explicit operator enablement.

## Status truth
Implemented code and tests must be reported separately from planned capabilities. GitHub Pages is a static frontend and does not host a persistent FastAPI worker by itself. A backend host, persistent storage, secrets and scheduled worker are required for continuous operation.
