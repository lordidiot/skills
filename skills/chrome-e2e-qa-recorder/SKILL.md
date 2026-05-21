---
name: chrome-e2e-qa-recorder
description: Run Chrome-based end-to-end QA flows from prose test instructions using Computer Use, stop on blockers or unsafe actions, and always export a video trace of the Computer Use screenshots and actions. Use when the user asks Codex to test a web app flow, smoke test common Chrome workflows, record QA steps, verify feature interactions visually, or produce an artifact-backed browser QA run.
---

# Chrome E2E QA Recorder

Use this skill to execute a prose-defined QA flow in Google Chrome and produce a video artifact from the current Codex session.

## Expected User Format

Prefer prose with these headings. Do not require YAML.

```text
Run a QA recording for: <flow name>

Start from:
<Initial URL/page/session/data state. Setup work is allowed to reach this state.>

Test steps:
1. <Concrete user-visible action.>
2. <Concrete user-visible action.>
3. <Concrete user-visible action.>

Must not:
<Forbidden actions, side effects, environments, or data mutations.>

Stop if:
<Conditions where the run should end instead of improvising.>
```

If the prompt has no actionable ordered steps, ask the user for the steps. Do not invent a feature flow.

## Recording Markers

Before the first Computer Use action, emit a normal assistant commentary message exactly like:

```text
CODEX_QA_RECORDING_START: <run-id>
```

After the last Computer Use action, even on failure or blocker, emit:

```text
CODEX_QA_RECORDING_END: <run-id>
```

Use a unique filesystem-safe run id, for example `qa-20260521-120845-saved-search`.

## Execution Workflow

1. Parse the user request into an internal checklist:
   - flow name
   - start state
   - ordered test steps
   - must-not constraints
   - stop conditions
2. State the checklist briefly before acting.
3. Emit the start marker.
4. Use Computer Use against `Google Chrome`.
5. Set up the start state first. Prefer a new tab when navigating to a new URL.
6. Execute the test steps in order.
7. After each step, inspect visible state with Computer Use and judge whether the UI responded enough to continue.
8. Stop immediately if:
   - a requested control or page cannot be confidently identified
   - continuing would violate a must-not
   - the app asks for CAPTCHA, unexpected 2FA, payment, production-impacting confirmation, admin approval, or risky browser permission
   - the flow reaches an unrecoverable error page or broken app state
9. Emit the end marker.
10. Run `scripts/export_chrome_qa_recording.py --run-id <run-id>` from any convenient workspace.
11. Return status, last completed step, blocker/failure if any, video path, manifest path, and copied session JSONL path.

Follow the Computer Use confirmation policy for risky actions. The user's QA prompt is not blanket permission to send messages, delete data, create accounts, change settings, make purchases, place orders, invite users, upload files, or transmit sensitive data unless it specifically approves that action and destination.

## Result Labels

Use one of:

- `PASS`: all requested steps were completed and the UI visibly responded.
- `FAIL`: a requested step was attempted but the app behavior was wrong or broken.
- `BLOCKED`: the run stopped because the next step was not actionable, unsafe, ambiguous, or required user takeover.

Failed and blocked runs must still export the video.

## Artifact Script

Use `scripts/export_chrome_qa_recording.py`.

Common commands:

```bash
scripts/export_chrome_qa_recording.py --run-id <run-id>
scripts/export_chrome_qa_recording.py --run-id <run-id> --duration 1.5
scripts/export_chrome_qa_recording.py --session-jsonl path/to/session.jsonl --run-id <run-id>
```

The script copies the current Codex session JSONL when needed, filters Computer Use frames between the recording markers, extracts embedded base64 screenshots, annotates each frame with the tool action arguments, and writes an MP4 plus manifest.
