# Incident log (sanitized)

Selected production incidents from the private system. Each one is now a test in this repository.
Account identifiers, balances and strategy parameters are removed.

The protocol for every incident: prove the root cause with evidence (logs, broker records, a control
test) before changing anything, add a regression test, then record the cause and the fix here.

| Incident | Root cause | Fix | Test |
|---|---|---|---|
| **Position ran unprotected after a fast reversal.** The protective stop was rejected and the position stayed naked until the session flatten. | The gateway rejected a stop whose price the market had already passed. Rejects were not captured, so nothing reacted. | Capture exchange rejects. If price is past the stop, flatten at market; otherwise resubmit once, then flatten. A full-history replay showed zero change to normal trades. | `test_rejected_stop_*` |
| **Urgent alert that never cleared** after a winning trade. | The target filled ~300 ms after entry, so the book went live to flat before the stop's cancel was confirmed. Verification only ran while live, so the check was orphaned. | Verify cancels in every state, including flat, and send a resolving message once confirmed. | `test_cancel_verification_continues_after_flat` |
| **Both protective legs could fill** in a whipsaw, leaving the account short after a long. | Cancelling an already-filled leg reports success, so the survivor-cancel looked clean. | Detect both legs filled, and any reversed net position, and flatten immediately. | `test_both_protective_legs_filled_is_flattened` |
| **Break-even stop move reported success but did not move the stop.** | The modify call returned OK while the working order kept its old price. | Read the order back after every modify. If the move did not take, arm a synthetic watchdog that flattens at market at entry. | `test_break_even_move_ignored_arms_synthetic_watchdog` |
| **Partial fills left part of a position unprotected.** | Protection was sized when the first fill arrived. | Size protection to what filled, and arm an additional bracket for each later increment. | `test_partial_fill_protection_sized_then_rearmed` |
| **End-of-day summaries silently lost for over a week.** Intraday pushes worked, so quiet evenings looked intentional. | The summary was sent on a daemon thread and the process exited in the same second, killing the request. Found by auditing service logs: no end-of-day delivery recorded across that period. | Flush outstanding sends before exit; block on the final summary. | `test_notifier_flushes_background_sends_before_exit` |
| **Six days of failed data pulls** with an authentication error, although a new key had been installed. | The key resolver checked an old key file before the environment file that held the new key. | One home for each secret; the stale file was retired. | (configuration) |
| **Gateway connections failing at the open.** | Two of the broker gateway's five DNS records were not reachable from the server. | Health-check the records and pin a healthy one; retry connects with a bounded backoff. | (operations) |
| **A backtest edge that was an artifact.** Several candidates looked strong and were not. | Fill and exit ordering that one-minute data cannot resolve. | The honest gate's two ordering checks, calibrated against a tick-tape replay. | `test_probe_power_fabricated_winners_are_caught` |
