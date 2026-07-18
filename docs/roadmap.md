# JARVIS AI OS — Implementation Roadmap

This consolidates the two "master prompt" visions (the 22-module Business
Operating System and the 20-module Jarvis AI OS with the dashboard mockup)
into one sequenced plan, mapped against what is already built and tested.
Rule carried over from every phase so far: **one phase at a time, verified
before the next begins.** The current gate is live Phase 3a validation —
nothing below the gate starts until it passes.

## Where things stand

| Layer | Status |
|---|---|
| Auth (JWT + refresh rotation, RBAC, sessions, audit) | ✅ built, tested |
| AI chat (Claude, streaming over WS, markdown, history, tool use) | ✅ built, tested |
| Long-term memory (hybrid semantic/keyword search, importance, aging) | ✅ built, tested |
| Workflow engine (Postgres queue, deps, retry, crash recovery, scheduling) | ✅ built, tested |
| 6 agents (amazon, finance, developer, research, operations, marketing) | ✅ built, tested |
| SP-API: Sellers, Orders, FBA Inventory, FBA Shipments, Financial Events | ✅ built, 91 tests vs. mocked API |
| **Live validation against the real seller account** | ⏳ **IN PROGRESS — the current gate** |
| Ads API, dashboard UI, Listings/Catalog/Pricing/Reports, everything below | ⏸ not started |

## Design north star: the dashboard mockup

The uploaded mockup ("JARVIS AI OS") is the Phase 3c design reference.
Captured here so the intent survives the conversation:

- **Sidebar** (animated, sectioned): AI Operations (Chat, Tasks &
  Automation, Computer Control, Files, Web Automation, Code Tools),
  Amazon Manager (Overview, PPC, Listings, Inventory, Sales & Profit,
  Reports, Customer Messages), Business Tools (Product Research,
  Suppliers, Finances, Alerts), System (Settings, Integrations, Logs).
- **KPI row**: Today's Sales, Est. Profit, ACOS, Total Orders, Units Sold —
  each with vs-yesterday delta and sparkline.
- **Main panels**: Sales Overview line chart (7-day, prior-period
  overlay), PPC Performance donut (ACOS + spend/sales/orders/impressions/
  clicks), AI Assistant card with proactive recommendations, Automation
  Center status tiles, System Resources meters, Recent Activity feed,
  Quick Actions grid, Recent Tasks with status, Upcoming Alerts with ETAs.
- **Style**: dark glassmorphism, glow accents, per-metric accent colors,
  responsive, command palette (Ctrl+K), top search.

Every number on that screen is SP-API or Ads API data. **The mockup cannot
be built honestly before Phases 3a-validated and 3b exist** — the project's
own rule ("never generate placeholder functionality; every widget must
load real backend data") makes live data a prerequisite of this UI, which
is why validation is the critical path to this screenshot, not a detour
from it.

## Architecture decisions (new stack proposals vs. existing code)

The latest master prompt names a partially different stack. Decisions,
with reasons — anchored on its own rule: *never break existing
functionality*:

| Proposal | Decision | Why |
|---|---|---|
| Next.js (replace Vite SPA) | **Keep Vite + React SPA** | This is a logged-in dashboard; SSR/SEO adds nothing. A framework migration is a rewrite risk with zero user-visible gain. The mockup's visual quality is fully achievable in the current stack. |
| Shadcn UI, Framer Motion, Zustand | **Adopt selectively in 3c** | Additive, low-risk; bring in during the dashboard build where they earn their place. |
| LangGraph (replace agent framework) | **Keep custom framework** | Existing planner/executor/validator/memory framework is built, tested, and integrated with tool permissions + audit. Swapping is a rewrite, not an upgrade. |
| Celery + Redis (replace workflow engine) | **Keep Postgres engine; revisit at scale** | Engine already does queues, retries, scheduling, crash recovery via `FOR UPDATE SKIP LOCKED` — no extra infra. If throughput ever demands it, revisit with data. |
| Redis cache | **Defer until a measured need** | No latency problem exists yet to solve. |
| Netlify | **No — doesn't fit** | Netlify hosts static/serverless JS; this backend is long-running FastAPI + Postgres + WebSockets. Deployment remains Docker Compose (already built); a VPS/cloud target can come later. |
| GitHub Actions CI | **Yes — first infra task after validation** | The repo already has 91 backend tests + strict TS build; CI is cheap and immediately valuable. |
| Computer/desktop control (mouse, keyboard, execute commands) | **Separate, security-gated track — last** | This is desktop-side software with a large attack surface, requiring the approval-workflow design the prompt itself demands. Not part of the web stack; scheduled after the business modules prove out. |

## Phase sequence

**GATE (now) — Phase 3a live validation.** Run `validate_amazon_live.py`
against the real seller account; reconcile Sellers/Orders/FBA
Inventory/Shipments/Financial Events with Seller Central; fix defects
until all VERIFIED. Nothing below starts first.

1. **3a fast-follows** — Listings sync (agreed deferral), then Catalog +
   Pricing + Reports API ingestion. GitHub Actions CI.
2. **3b — Amazon Ads API (PPC)** — campaigns, ad groups, keywords, search
   terms, negatives; ACOS/TACOS/ROAS/CTR/CVR/CPC/spend; date filters;
   bid/placement data; AI recommendations via the PPC-capable agent.
   *(Gated on 3a validation passing — standing instruction.)*
3. **3c — Dashboard UI (the mockup)** — sidebar, KPI row, charts, AI
   assistant panel, automation center, activity/alerts; command palette;
   responsive; built on live 3a+3b data. Refresh button and sync-status
   surfacing land here.
4. **3d — Automation & reporting** — scheduled daily/weekly reports, alert
   rules (low stock, budget, Buy Box), PPC/restock recommendations as
   reviewable tasks, export (PDF/Excel/CSV).
5. **4 — Business modules** (one at a time, order by business value):
   Suppliers & POs → Finance (P&L, cash flow, margins) → Product Research →
   Documents/Files (upload, OCR, AI summaries) → CRM/Customer Messages
   (note: buyer PII sits behind SP-API Restricted Data Tokens — needs its
   own approval) → Notification channels (email first, then
   Slack/Telegram/WhatsApp).
6. **5 — Voice & advanced chat** — voice input/output, wake word; chat
   upgrades (file/PDF/CSV upload, multi-provider, cost tracking).
7. **6 — Web automation** (Playwright browser agent, approval-gated), then
   the **desktop control track** with its security design.

Each phase ends the same way every prior phase has: tests green, lint
clean, migrations verified, committed, pushed — then stop for explicit
approval before the next.
