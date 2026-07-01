---
description: Manually capture the current conversation as memory via mnemoq --capture-file.
---

## Steps

### 1. Write conversation to temp file

Reconstruct the current conversation (your prompts + Cascade's responses) as plain text in this format:

```
Human: <your first prompt>
Agent: <Cascade's first response>
Human: <your second prompt>
Agent: <Cascade's second response>
...
```

Write this to a temp file using `write_to_file`:

- **Windows:** `%TEMP%\capture-conv.txt`
- **macOS/Linux:** `/tmp/capture-conv.txt`

### 2. Run capture

Run the capture command:

```bash
python -m mnemoq.cli --capture-file /tmp/capture-conv.txt
```

```powershell
python -m mnemoq.cli --capture-file $env:TEMP\capture-conv.txt
```

### 3. Report results

Show the user:
- Extraction tier used (heuristic/online/offline)
- Number of summaries extracted
- Auto-logged entries (type + trigger)
- Any suggestions

### 4. Clean up

Delete the temp file:

```bash
rm /tmp/capture-conv.txt
```

```powershell
Remove-Item $env:TEMP\capture-conv.txt
```

## Limitations

This workflow reconstructs the conversation from Cascade's context window, which is inherently less complete than the automatic transcript hook (`post_cascade_response_with_transcript`). Use it for manual re-capture after heuristic changes or when the automatic hook is not installed.
