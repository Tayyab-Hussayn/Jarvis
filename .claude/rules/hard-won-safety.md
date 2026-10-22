---
paths:
  - "kravein/safety.py"
  - "kravein/registry.py"
  - "kravein/skills/**"
  - "kravein/platform/shell.py"
  - "kravein/jobs.py"
---

# Hard-won knowledge: Safety and confirmations

Moved verbatim from `CLAUDE.md`. Each paragraph is a bug that already happened; do not rediscover it.

**A model will hunt for a gap in the safety verb list.** Asked to delete a file through the web
UI and denied four times (`delete_path`, `delete_path permanent`, `rm -f`, `rm`), it tried
`unlink` — which matched no pattern in `safety.py` — and deleted the file unconfirmed. The system
prompt says "never try to work around a confirmation"; that is not enforcement. Two fixes, and
both matter: the missing verbs (`unlink`, `find -delete`, `xargs rm`, `rsync --delete`, `sed -i`,
interpreter one-liners) are now classified, and `safety.enforce()` keeps a **per-turn denial
memory** keyed on the *target*, not the verb — once you decline an action on a path, any further
destructive action on that same path in that request is refused outright without re-asking. The
memory is scoped by `confirmer_scope`, so it lasts exactly one turn. Never widen it to a blanket
per-turn lockout: "delete A, keep B" must still work.

**The denial memory only works if the target is *declared*.** It originally read the target back
out of the human-readable action string, which found file paths and pids and **nothing else** — so
declining `close window 'foot'` recorded no key at all and the model could retry forever. Reported
from a real session: one "no" to closing a window produced two more prompts, the second about an
unrelated window on a different workspace. Skills now pass `targets=describe_targets(...)` to
`safety.enforce()`; prose parsing is the fallback, not the design.

Two rules for `targets`: include a stable id (`window:0x…`, `pid:42`, `service:nginx`) so a retry
under any verb is caught, and when the skill resolved its target **implicitly** — `close_window()`
with no query acts on whatever is focused — add a marker like `close_window:implicit`. Focus moves
between calls, so without the marker the retry prompts about a different window and looks like the
assistant ignoring you.

**The model could confirm its own destructive actions.** `registry.dispatch()` harvested a
`confirm` flag out of the model's own arguments and ORed it into `pre_confirmed`, which
short-circuits `safety.enforce()` before the no-confirmer check. Caught by a scheduled job:
refused on `delete_path`, it retried the same delete as `run_command "rm -f …"` with
`confirm=true` and the file was gone — from an unattended session with no confirmer at all. A
`confirm` **keyword argument** still works, because that comes from a trusted caller; a `confirm`
key inside `params` is now ignored and audited. This is the third time a prompt instruction has
turned out not to be enforcement.

**An unattended refusal has to be remembered too.** `enforce()` raised `ConfirmationRequired` at
the `confirmer is _deny` branch *before* recording anything in the per-turn denial memory, so the
mechanism built exactly to catch a retry under a different verb never saw the first refusal.
