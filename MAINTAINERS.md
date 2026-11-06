# Maintainers

- **Owner:** [@23444555](https://github.com/23444555) — hands-off by declaration.
  The owner sets direction, states the goal, and does the final accept/reject.
  The owner does not write code, run experiments, or maintain day-to-day.
- **Implementation:** an AI coding agent working under the owner's standing instruction
  to decide and execute, then report results — escalating only what is irreversible or
  outward-facing (publishing a package, deleting data, spending money, force-pushing).

## What that means in practice

- Issues and pull requests are triaged and answered by the agent; the owner has the last word.
- Releases are prepared by the agent and approved (tag / publish) by the owner.
- Report progress is the deliverable: every claim in `docs/` should be reproducible from a
  command in this repository, and negative results are kept, not softened
  (see `packages/eventfold/docs/EVALUATION.md`).
- The owner's role in a dispute is a single yes/no, not a review cycle. If a decision is
  reversible and in scope, the agent makes it and says so afterwards.

## Do not wait for the owner

The owner does not read status updates and will probably not reply to routine questions.
Silence is not a blocker and is not a no: it means "you decide".

- Ask first only when the action is irreversible or outward-facing (publishing a package,
  deleting data, spending money, rewriting public history).
- Everything else: decide, execute, test, and report what happened afterwards in one
  message.
- Never park work waiting for an answer that will not come. The default answer is
  "go ahead".

