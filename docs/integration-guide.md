# Integration Guide — Closing the Learning Loop

MnemoQ is IDE- and agent-agnostic. Any tool that can run a shell command can
feed the engine. This guide describes the full **retrieve → work → log →
evaluate → auto-learn** loop and how to wire it into any agent or IDE.

The engine is only as useful as the entries it accumulates. Retrieval already
works the moment you scaffold a project; the value comes from *writing back*
what each session learns. The four steps below make that habitual rather than
optional.

---

## The loop at a glance

| Phase | When | Command |
|-------|------|---------|
| **Retrieve** | Before starting a task | `mnemoq --step N --components X,Y --domain Z` |
| **Log** | During work, on any non-obvious discovery | `mnemoq --log-file entry.json` |
| **Evaluate** | At session end, with a structured summary | `mnemoq --evaluate-file summary.json` |
| **Auto-learn** | After every commit (automatic) | git `post-commit` hook → `mnemoq --auto-learn` |

---

## 1. Before work — retrieve

Pull the learnings relevant to what you're about to do. Pass the plan step, the
components you'll touch, and a coarse domain tag:

```bash
mnemoq --step 12 --components AuthService,TokenStore --domain backend
```

The engine returns scored warnings (critical) and patterns (non-critical),
ready to inject into the agent's context.

## 2. During work — log discoveries

Whenever the session surfaces something non-obvious — a gotcha, a fix, a
decision, a workaround — log it. Build a JSON entry and write it via
`--log-file` (PowerShell-safe; avoids shell quoting issues):

```bash
mnemoq --log-file entry.json
```

```json
{
  "step": 12,
  "source_agent": "cascade",
  "type": "bug_fix",
  "domain": "backend",
  "components": ["TokenStore"],
  "files_touched": ["src/token_store.py"],
  "trigger": "When refreshing tokens under concurrent requests",
  "action": "ALWAYS acquire the row lock before reading the refresh counter",
  "reason": "Two requests read the same counter and both refreshed, invalidating each other",
  "importance": 8,
  "severity": "major"
}
```

Notes:
- `source_agent` accepts any name by default (the whitelist is opt-in via
  `valid_source_agents` in `config.json`). Use whatever identifies your agent —
  `cascade`, `claude`, `copilot`, etc.
- `trigger` must start with **When**; `action` must contain **ALWAYS** or
  **NEVER**. These are the structural quality gates.
- Near-duplicate entries are folded into the existing entry's
  `reinforcement_count` rather than duplicated, so logging the same lesson
  repeatedly *strengthens* it.

## 3. After work — evaluate a session summary

At the end of a session, hand the engine a structured summary of what happened.
Heuristic detectors score it and **auto-log** anything at or above the
configured threshold (`evaluate_auto_log_threshold`, default `0.5`), turning
the agent's narration into durable entries without hand-building each one:

```bash
mnemoq --evaluate-file summary.json
```

```json
{
  "step": 12,
  "prompt_type": "human",
  "outcome": "correction",
  "text": "what happened in this session",
  "corrected_action": "what should have been done",
  "rejected_action": "what was wrong",
  "components": ["AuthService"],
  "files_touched": ["src/auth.py"]
}
```

Fields:
- `prompt_type`: `human` | `agent`
- `outcome`: `correction` | `preference` | `bug_fixed` | `decision` | `workaround` | `none`
- `corrected_action` / `rejected_action`: used by the human-correction detector
- `text`: free-form description; keywords like *always*, *never*, *remember*
  raise detector confidence

Detector confidences: human correction `0.95`, explicit remember `0.85`, bug
fixed `0.70`, decision `0.60`, workaround `0.55`. At the default threshold of
`0.5` all of them auto-log; raise the threshold in `config.json` if that is too
noisy for your project.

## 4. Post-commit — auto-learn (automatic)

Install the git hook once per clone:

```bash
mnemoq --install-hooks
```

After that, every commit triggers `mnemoq --auto-learn` in the background. It
mines git history and corpus/retrieval metrics for patterns (repeated fixes,
reverts, under-retrieved or over-injected entries, conflicts, retrieval
failures) and logs what it finds. The hook never blocks or slows the commit;
errors are appended to `<git-dir>/mnemoq-auto-learn.log`.

> Auto-learn detectors need some scale before they fire (repeated fix commits,
> accumulated reinforcement). On a fresh corpus, steps 2 and 3 are what actually
> grow the memory; auto-learn becomes productive as history builds up.

---

## Wiring it into an IDE / agent

The loop is just four shell commands, so any agent runtime can drive it:

- **Session start hook** → run the retrieve command, inject results into context.
- **On discovery** → write an entry file and call `--log-file`.
- **Session end hook** → emit a summary JSON and call `--evaluate-file`.
- **Git post-commit** → installed automatically via `--install-hooks`.

For MCP-native clients (Claude Desktop, Cursor, Windsurf, VS Code), the same
retrieve/log/resolve/consolidate operations are exposed as MCP tools — see the
[MCP integration guide](mcp-integration.md). The CLI loop here is the
lowest-common-denominator path that works everywhere else.
