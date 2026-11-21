# TrustGap stage run sheet

For the presenter.
The slot is 5 minutes with a hard stop.
All timings below were measured in rehearsal runs.

## The one command

```sh
scripts/demo.sh --no-tour
```

It seeds fresh databases, starts the bank and the breaker, opens the dashboard in the background, and waits for Enter between beats.
With `.env` in place it uses the real Jev and starts a real Devin session on the attack.
Without the tour the machine time is about 2 min 40 s, which leaves room to talk.
With the tour it is about 3 min 30 s.

## Keys

| Key | When | What it does |
|---|---|---|
| Enter | at a prompt | next beat |
| `f` then Enter | case 4, while waiting for Devin | show the pull request from the rehearsal run, and say so on screen |
| `s` then Enter | case 4, while waiting for Devin | skip the wait |
| `q` then Enter | after the last beat | stop the services and exit |
| Ctrl-C | any time | stop everything, and print the command to continue |
| `C` | in the dashboard tab | subtitles on or off |

An Enter typed while a step is running is ignored, so a double press does not skip a pause.
After the last beat the services stay up and the final screen stays on the projector until you press `q`.

## Five minutes

| Time | Key | On screen | Say |
|---|---|---|---|
| 0:00 | start the command | Case 1 card | Rate limiters count requests. We count money and behaviour. Three layers decide. |
| 0:25 | Enter | 42 payments stream, 42 of 42 executed | A normal business day. The breaker is invisible. |
| 0:40 | Enter | Case 2 card | 02:10 at night, AED 48,000 to a known supplier, with an approved invoice. |
| 0:50 | Enter | Layer 1 soft stop, Jev scores, release | Jev answers two questions. The thresholds and the 1 second limit are ours. |
| 1:15 | Enter | Case 3 card | A stolen API key. |
| 1:25 | Enter | 6 small payments leak, soft stops, KEY PAUSED on payment 9 | Every later payment is held, and the webhook just called Devin. |
| 2:00 | Enter | Case 4 card | Devin investigates and proposes. It never moves money. |
| 2:10 | Enter, then `f` and Enter after the webhook caption | The rehearsal pull request, the verdict | Devin needs about 10 minutes. This pull request is from Devin on the same incident in our rehearsal. |
| 2:35 | Enter | Case 5 card | Proof: we replay the same attack. |
| 2:42 | Enter | Before: 6 of 42 executed. After: 0 of 42. Legitimate: 42 of 42. | The leak is closed. No legitimate payment is held. |
| 3:00 | no key | The final screen stays | Closing words. |
| 3:30 to 5:00 | | | Buffer. |

If Devin's real verdict arrives before case 4, the runner shows it and you do not need `f`.
In the one real run so far Devin took 9 min 25 s, so plan on `f`.

## If something breaks

| Problem | What to do |
|---|---|
| The runner stops with an error, or you pressed Ctrl-C | Run the command it printed, for example `scripts/demo.sh --keep-data --from 4`. |
| You need a clean restart from the attack | `scripts/demo.sh --no-tour --from 3`. About 1 min 50 s to the end. |
| "port 8000 is busy" | An old run is still up. `lsof -nP -iTCP:8000 -iTCP:8001 -sTCP:LISTEN`, then stop those processes. |
| Jev does not answer in case 2 | The payment stays on hold and the caption says so. Say: "No answer in one second, so the hold stays. That is the design." |
| The webhook to Devin fails | The caption says so. Press `f`. |
| No network at all | Plan B below. |

## Plan B: no network

Decide before you start.

```sh
JEV_MODE=stub scripts/demo.sh --no-tour --no-webhook --no-watcher
```

The beats are the same.
In case 2 say "an offline stub of Jev", because the card shows "stub" and 0 ms.
In case 4 the caption says there is no webhook: press `f` at once.
The fallback works offline from `.state/pr/`.
The simulated walk-through `docs/demo_preview.html` needs nothing at all.

## Before you go on stage

Run these in order, from the repo root.

1. Do not merge Devin's rehearsal pull request.
   Its rule closes the gap, and case 3 would leak nothing.
2. Update and test, on a quiet machine:
   ```sh
   git checkout main && git pull
   .venv/bin/python -m pytest -q
   scripts/infra_check.sh        # must print "infra check: clean"
   ```
   If `infra_check.sh` is not clean, see "Before the demo" in `docs/REFERENCE.md`.
3. Check the secrets file: `.env` has `TYPESAFE_API_KEY`, `DEVIN_WEBHOOK_URL` and `DEVIN_WEBHOOK_SECRET`.
   Values set on the command line win over `.env`.
4. Check Jev on the venue network:
   ```sh
   set -a; . ./.env; set +a
   .venv/bin/python scripts/jev_eval.py --n 3    # all 8 cases must pass
   ```
   If the network is slow, use a phone hotspot.
5. Check the fallback pull request is saved:
   ```sh
   scripts/watch_pr.sh --pr 1 --no-apply --save-as rehearsal
   scripts/replay.sh --rules-dir .state/pr/1/rules   # both lines must say PASS
   cp -R .state/pr .state/pr.backup
   ```
6. Check nothing is on the ports: `lsof -nP -iTCP:8000 -iTCP:8001 -sTCP:LISTEN` prints nothing.
7. Dry run without spending a Devin session: `scripts/demo.sh --no-tour --no-webhook`, press `f` in case 4, end with `q`.
8. Laptop: `caffeinate -dims &`, Do Not Disturb on, quit Slack, Zoom and other heavy apps, browser zoom at 100%.
   Under heavy load each replay took 40 seconds instead of 3.
9. Screens: the dashboard tab full screen on the projector, the terminal on the laptop screen with a large font.
   Keep the keyboard focus in the terminal.
10. Type the command and wait: `scripts/demo.sh --no-tour`.

## A second terminal

`scripts/demo.sh` creates random admin and clock tokens for each run.
To use `scripts/apply_verdict.sh` or `scripts/watch_pr.sh` from another terminal, load them first:

```sh
set -a; . .state/demo/tokens.env; set +a
```
