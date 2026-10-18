# Antigravity — experimental adapter (0.1.12)

Implemented for the documented native hook contract; not yet validated against an installed Antigravity. This PC does not have it installed. No Antigravity software or global configuration was installed during development.

The [official hook documentation](https://www.antigravity.google/docs/hooks/) describes App, CLI and standalone IDE hooks, including a global `~/.gemini/config/hooks.json`. A global definition applies across workspaces; Beacon does not require opening every project with a special command. Workspace trust and enabled hooks in Antigravity still matter.

## Connect on a tester's Windows PC

Update Android to 0.1.12 first. Earlier clients do not accept the new provider enum. Run from the Agent Beacon project directory with the existing Python environment:

```powershell
python -m agent_beacon.adapters.configure antigravity --state-dir .beacon --output-dir .beacon/generated
python -m agent_beacon.adapters.configure antigravity --state-dir .beacon --install
```

The first command generates a reviewable example. The second only prints a plan. After reviewing it, install the definition:

```powershell
python -m agent_beacon.adapters.configure antigravity --state-dir .beacon --install --apply
```

This source-launch example uses `.beacon`, matching the existing Python launcher. The Windows EXE wizard uses its own per-user data directory instead. This merges one named `agent-beacon-status` definition into the global hooks file. Other definitions are preserved; existing files receive backups. Recognized Beacon definitions can migrate to the installed helper; conflicting/custom definitions are refused. Enable/reload hooks in Antigravity as required by that app. Do not install the same definition globally and locally, which would duplicate events.

For the source launcher, enable the provider explicitly. Stop only the instance owned by the launcher record, then restart it:

```powershell
& .\scripts\windows\Stop-AgentBeacon.ps1
& .\scripts\windows\Start-AgentBeacon.ps1 -Lan -Port 8766 -Integrations codex,claude,antigravity
```

Use the port appropriate for the tester's installation. The launcher refuses to stop unrelated processes and looks for another free port when needed. Its default providers remain Codex and Claude Code, so existing users without Antigravity are not enrolled in an unavailable observer. Android's Check & connect uses the existing pairing and authenticated rediscovery.

In Android, open **Widget settings**, choose the desired **Agent overview**, select Antigravity and any other agents, arrange rows and save. Each widget keeps its own selection. A selection controls display, not desktop hook installation or app-wide notifications.

## Behavior implemented

| Native signal | Beacon behavior |
| --- | --- |
| SessionStart | Enroll without inventing ready |
| PreInvocation / PostInvocation | Working; model invocation completion is not task completion |
| PreToolUse of `ask_question` | Needs you |
| Matching PostToolUse of that question | Resume and clear the question reminder |
| Stop with fullyIdle=false | Keep working; background tasks remain |
| Stop with fullyIdle=true | Ready for another request, including a stopped/failed execution; this does not certify success |
| Missing required counters / idle field | State unconfirmed |

Native progress counters fence duplicates and older events within each counter family. Late tools and PostInvocation cannot resurrect a terminal ready state; a newer PreInvocation is required. The existing one-second PC stabilization, idle-chat aging, history, SSE and Android alert deduplication also apply. Start and heartbeat remain quiet; questions and completion use existing attention alerts.

Only bounded lifecycle metadata, conversation ID, process identity, counters and the first workspace's basename enter the local queue. Prompt text, transcripts, raw paths, tool arguments/results and error strings are not persisted. Hook stdout is fail-open: PreToolUse returns `allow` to delegate to normal permissions; Stop permits stopping; other hooks return an empty object. Beacon never auto-approves a tool or forces the agent to continue.

## Limits and acceptance checks

- Process ancestry must contain a branded `antigravity`/`agy` executable, with matching OS creation identity. Generic Node/Chrome processes are deliberately insufficient. Some CLI wrappers may therefore need additional validated identity support; do not advertise universal CLI coverage yet.
- Conversation IDs are not native per-request IDs. Invocation, tool and execution counters are separate. Arbitrary cross-family delayed/unseen Stop events and counter resets after worker restart still require live validation; the adapter does not claim perfect turn correlation.
- The hook contract used here provides no chat title. Beacon shows a friendly untitled-chat fallback and project basename, never an opaque hash as the title. No transcript scraping was added.
- Process loss makes state unconfirmed; a confirmed exited ready worker may close monitoring. Closing monitoring does not delete the chat in Antigravity.
- iOS's current source parser is not upgraded for this provider; iOS work is postponed. Keep Antigravity disabled for old clients.

Tester checks: new and resumed chats; two chats in one project; real question/answer; background command still running after model completion; explicit stop; normal completion; worker restart; hooks disabled; PC restart and phone reconnect. Verify both App and IDE/CLI variants that the tester uses and record executable identity without command lines or message content. These physical checks are open.

Host replay passed 76 adapter/config/command-output checks and 784 Android parsing/filter/alert assertions. Existing Python 119 and browser 23 checks passed. Host fixtures and exact generated Windows command execution do not replace a native Antigravity test.

To disable, deselect this provider in the launcher integrations and disable the named hook in Antigravity's settings. Preserve other hooks and paired-device state. Backup before this update: `.beacon/backups/before-0.1.12/`.
