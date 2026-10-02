"""Claude via the Claude Code CLI (`claude -p`), billed to your Claude subscription.

Auth: locally, your normal `claude` login. In GitHub Actions, the CLAUDE_CODE_OAUTH_TOKEN
secret created with `claude setup-token`. (Don't add --bare: bare mode ignores that token.)
"""
import json
import os
import shutil
import subprocess
import tempfile

from .config import ROOT

TIMEOUT = 15 * 60  # seconds per call; research with several web searches can take a few minutes


def _claude_bin() -> str:
    path = (os.getenv("CLAUDE_BIN") or shutil.which("claude")
            or shutil.which(os.path.expanduser("~/.local/bin/claude")))
    if not path:
        raise RuntimeError("Claude Code CLI not found. Install it: https://code.claude.com/docs/en/setup")
    return path


def run(cfg: dict, prompt: str, system: str, schema: dict, tools: list[str] | None = None,
        effort: str | None = None, max_turns: int = 30) -> dict:
    """One headless Claude Code run. Returns the JSON object matching `schema`."""
    tools = tools or []
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(system)
        system_file = f.name
    cmd = [
        _claude_bin(), "-p", "Follow the instructions in the input.",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--model", cfg["copy"]["model"],
        "--fallback-model", cfg["copy"].get("fallback_model", "sonnet"),
        "--system-prompt-file", system_file,        # replace the coding-assistant prompt
        "--tools", ",".join(tools),                 # only the tools this step needs ("" = none)
        "--permission-mode", "dontAsk",             # nobody is there to answer prompts
        "--strict-mcp-config",                      # no MCP servers from your local setup
        "--disable-slash-commands",
        "--no-session-persistence",
        "--max-turns", str(max_turns),
    ]
    if tools:
        cmd += ["--allowedTools", ",".join(tools)]
    if effort:
        cmd += ["--effort", effort]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                              cwd=ROOT, timeout=TIMEOUT)
    finally:
        os.unlink(system_file)

    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"Claude Code failed (exit {proc.returncode}): "
                           f"{(proc.stderr or proc.stdout).strip()[:800]}")
    if out.get("is_error") or out.get("subtype") != "success":
        raise RuntimeError(f"Claude Code run failed ({out.get('subtype')}): {str(out.get('result'))[:800]}")
    data = out.get("structured_output")
    if not isinstance(data, dict):
        raise RuntimeError("Claude Code returned no structured output")
    return data
