# 10 · How the owner works (follow this exactly)

## Hard rules

1. **Never deploy.** The owner runs `./redeploy.sh` themselves, when they
   decide. An agent commits and pushes to the working branch only.
   ("I did not do this docker change so remember I am not deploying it now.
   I will ask later on.")
2. **Branch**: `claude/nifty-shop-port-setup-xkdq22`. Push with
   `git push -u origin <branch>`. Don't open a pull request unless asked.
3. **Tests for everything.** "Add test cases for everything. If you find any
   issue add a test case immediately." Every bug fix gets a test that fails
   without the fix (prove it by putting the bug back).
4. **The gate decides.** Before every push of code:
   `PYTHON=.venv/bin/python E2E_SHARDS=1 ./run_tests.sh --e2e` must print
   **SAFE TO DEPLOY**. Doc-only changes don't need the gate. If you push
   without the gate (the owner sometimes asks for speed), say so plainly.
5. **Step by step.** "Make sure after each step you test and push and then
   start the next one." Multi-part requests: do them in the owner's order,
   gate and push each before the next.
6. **Report issues in earlier work.** "If there are any issues in the previous
   one, do this and highlight at the end. If all good then mention that as
   well." Every report ends with what was found in earlier work, or says
   nothing was found.
7. **Discuss first when asked.** When the owner says "first tell me, don't
   implement" or "do not code once you get it, let me know", stop at the plan.
   When unsure about trading behaviour, ask; never change a live strategy's
   trading logic without approval. Logging-only changes need a test proving
   orders are unchanged.
8. **All trades are paper.** Never place a real order or write code that does,
   unless the owner explicitly starts that work.
9. **Don't break current flows.** New backend APIs go under `/v2`; v1 keeps
   working. Classic UI stays as a fallback.
10. **Commit messages** describe the change plainly and end with the
    attribution trailer the session gives you. No model names in commits, PRs
    or code.

## How to talk to the owner

- **Honest and plain.** Say what's measured vs estimated, what was tested and
  what wasn't ("tested on simulated data, not real Kite"). Never call
  something fixed without checking it against the real case. The owner lost
  patience on 21 Oct when "fixes" for the repair script failed against real
  data repeatedly; the lesson: reproduce the exact failure first, test against
  their real output, and count Kite requests.
- **No gimmicks.** The owner rejected "AI slop" designs, mascots, generic
  feature lists. Recommend one thing with reasons; don't survey options you
  won't pursue.
- **Plain trading words**, not system words. Verdicts in words ("In loss",
  "Would be rejected: lot size").
- **Lead with the answer**, then details. Give exact commands to run on the
  box, with `--dry-run` first for anything that writes.
- **Correct yourself openly** when the owner pushes back and you were wrong.
- **Don't assume a provider.** Never write a cloud, a region or a vendor's
  managed service into the design as if it were chosen. Name the protocol,
  give the self-run option, list managed ones as alternatives. On 25 Oct the
  owner found AWS assumed across every architecture doc, video and picture:
  "Did I tell you?" They hadn't. Linck Platform must deploy on any cloud or
  own servers, mixed.
- When the owner sends screenshots of bugs, fix that bug **and** look for the
  same class of bug elsewhere (e.g. one green-for-loss bug → check every
  chart).

## UI rules the owner holds to

Phone-first for monitoring; no sideways scroll; no overlapping text; green/red
only for P&L; one y-axis per chart; every chart hoverable; both themes with
equal care; ₹ formatting; risky actions say what will happen; screenshots at
desktop (1440) and phone (390) checked before calling UI work done; modular
code (one list to extend for themes, prefs, pages, rules).

## Environment notes (cloud sandbox)

- Postgres may stop: `service postgresql start`. Test DB `t_test` (role `t`,
  CREATEDB). `.venv` has dev deps. Playwright Chromium at
  `/opt/pw-browsers`.
- Kite, NSE and zerodha.com are unreachable from the sandbox; real Kite
  behaviour is only verified by the owner on their box.
- Scratch work goes in the session scratchpad, not the repo.
