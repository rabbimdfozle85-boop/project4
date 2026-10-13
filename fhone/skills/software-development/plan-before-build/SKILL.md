---
name: plan-before-build
description: "Use when asked to plan or design before implementing."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [planning, design, approval, scope, architecture, workflow]
tier: 1
---

# Plan before build

Applies whenever the user asks for a plan, a design, an architecture, a report,
or a "how should we build this" — instead of asking for working code.

## When to Use

- The user asks for a plan, design, architecture, or report rather than code.
- The user says "first, …" — signalling a next step they will direct.
- A multi-session design project where each stage needs sign-off.
- You are about to write implementation code for something that was *described*
  rather than *requested*.

Not for: a direct request to build, fix, or change something. Then, build it.

## The rule

The deliverable is the document. Write it, hand it over, stop. Do not begin
implementation until the user gives an explicit go-ahead.

**Your own offer is not approval.** "Want me to scaffold this?" answered with
anything short of an explicit yes is not a green light. Neither is the user
sending you the information you asked for — probe output, device specs, config
dumps, requirements. Supplying inputs to the plan is not approving the build.

What this prevents: a long build the user has to unwind, and the review step
they were actually buying. The cost of waiting is one message; the cost of
overstepping is a trust deficit that outlasts the code.

## Procedure

1. **Gather facts before designing.** If the plan depends on the environment
   (hardware, versions, limits, existing config), get real numbers first — ask
   for the probe commands or run them. A plan written on assumed specs is
   guesswork dressed as analysis.
2. **Ask blocking questions, or mark them as open decisions.** A question whose
   answer changes the architecture belongs *before* the plan. Anything
   non-blocking becomes an explicit "open decisions" list at the end.
3. **Write the plan as a document**, placed outside the implementation tree so
   that deleting the code later does not delete the plan. State the path in
   chat and give a short summary — not the whole document inline.
4. **Stop.** No code, no scaffold, no "I'll just start Phase 1 while you read."
5. **On explicit approval, build.** Only then.

## Validation work is allowed — but label it

Building a throwaway harness to *validate a design assumption* (measure the real
footprint, confirm an API shape, prove a parser handles a real wire format) is
legitimate design work and produces evidence a plan alone cannot. Two conditions:

- Present it as validation, in those words — "measured on a validation harness
  at `<path>`" — never as if the product exists.
- Keep it outside the real project tree, and offer to delete it once the plan is
  approved.

Never present validation code as a deliverable, and never let it quietly become
the implementation. If the user later says "keep it for testing," that is a
decision they make, not one you assume.

## Correct the premise before designing on it

When a spec rests on a false assumption, say so plainly and fix the assumption
first. Designing on top of a wrong premise yields a plan that cannot work, and
the user has no way to detect it.

State the correction as fact plus mechanism, then give the design that follows
from the corrected premise. Do not hedge a correction into mush, and do not
silently design around it — a silent workaround leaves the user believing their
premise held.

## Re-baseline when constraints change mid-plan

When the user changes a constraint (a budget, a target platform, a requirement),
state in the same reply what the change invalidates and what it unlocks. A plan
that still honours a constraint the user just lifted is stale, and one that
silently keeps an old ceiling produces a worse design than they asked for.

## Deliverable shape

- Architecture, module responsibilities, data model, risks, phases.
- Risks ranked, each with a mitigation. An unmitigated risk list is decoration.
- Numbers marked **measured** or **estimated**, never blurred together. Cite the
  script or command that produced a measurement.
- Open decisions at the end, each one answerable.
- No implementation detail the user did not ask for.
- **Enumerating a large existing inventory?** Ship a generator script that reads
  the real source and emits the catalog, rather than hand-writing the entries.
  A hand-written enumeration goes stale on the first upstream change and nobody
  re-checks it; a generator is re-run.

## Pitfalls

- **Reading "first" as "and then".** "First, give me X" means the next step is
  another question, not a build.
- **Treating enthusiasm as approval.** A user excited about the direction has
  approved the *direction*, not the implementation.
- **Burying the correction.** A false premise corrected in passing, halfway
  through, reads as agreement. Lead with it.
- **Output size is set by the content, not by the request.** A plan the user
  asked to be short is not an invitation to write 20 pages, and one the user
  asked to be 3 MB is not an invitation to pad. When a requested size defeats the
  artifact's purpose, quantify it against a known reference, say what padding
  costs — duplicated sections bury the decisions the user is meant to review and
  drift from reality the moment implementation starts — then deliver the size the
  content needs and say why. Volume does not predict implementation quality;
  precise contracts and acceptance tests do. Offer the honest way to add more:
  per-subsystem specs with real content.
