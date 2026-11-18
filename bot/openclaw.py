"""The one place the bot shells out to the `openclaw` CLI, and the one seam tests stub.

Command and output shapes match OpenClaw 2026.10.4 as run live; the one shape still a
guess is marked UNVERIFIED.
"""
import json
import subprocess

CHANNEL = "discord"
GUILD = "1537182496029020232"
BOOKS_CHANNEL_ID = "1537343965538426951"
BOOKS_CHANNEL = f"channel:{BOOKS_CHANNEL_ID}"
MAX_POST = 1900  # under Discord's 2000-character message limit


class OpenClawError(Exception):
    """An openclaw command failed, or its output held nothing usable."""


def call(args: list[str]) -> str:
    """Run `openclaw <args>` and return its stdout; raise OpenClawError on a nonzero exit.

    The error carries the `--json` reply's `error.message` when there is one: stderr only says
    "The CLI command failed."."""
    try:
        done = subprocess.run(["openclaw", *args], capture_output=True, text=True, check=False)
    except FileNotFoundError as e:
        raise OpenClawError("openclaw is not on PATH") from e
    if done.returncode != 0:
        out = _json(done.stdout)
        error = out.get("error") if isinstance(out, dict) else None
        reason = error.get("message") if isinstance(error, dict) else None
        if not (isinstance(reason, str) and reason.strip()):
            reason = next((line for line in done.stderr.splitlines() if line.strip()), "no error output")
        raise OpenClawError(f"exit {done.returncode}: {reason.strip()}")
    return done.stdout


def chunks(text: str, limit: int = MAX_POST) -> list[str]:
    """Split text into pieces of at most `limit` characters, breaking at line ends where possible."""
    out: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                out.append(current)
                current = ""
            out.append(line[:limit])
            line = line[limit:]
        if not current:
            current = line
        elif len(current) + 1 + len(line) <= limit:
            current += "\n" + line
        else:
            out.append(current)
            current = line
    if current or not out:
        out.append(current)
    return out


def post(thread_id: str, text: str) -> None:
    """Post text into a Discord thread, in as many messages as Discord's limit needs."""
    for piece in chunks(text):
        call(["message", "send", "--channel", CHANNEL, "--target", f"channel:{thread_id}", "--message", piece])


def _json(stdout: str) -> object:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def _layers(obj: object) -> list[dict]:
    """The object itself plus any `result` / `payload` wrapper inside it."""
    if not isinstance(obj, dict):
        return []
    found = [obj]
    for key in ("result", "payload"):
        inner = obj.get(key)
        if isinstance(inner, dict):
            found.append(inner)
    return found


def created_thread_id(stdout: str) -> str:
    """The new thread's id from `message thread create --json`, or "" when it is not there."""
    # UNVERIFIED: the id key; tried as `threadId`, then `thread.id`, then `id`.
    for layer in _layers(_json(stdout)):
        thread = layer.get("thread")
        for value in (layer.get("threadId"), thread.get("id") if isinstance(thread, dict) else None, layer.get("id")):
            if isinstance(value, (str, int)) and str(value):
                return str(value)
    return ""


def listed_threads(stdout: str) -> list[tuple[str, str]]:
    """(id, name) for each thread in `message thread list --json`, whose list is at `payload.threads.threads`."""
    obj = _json(stdout)
    payload = obj.get("payload") if isinstance(obj, dict) else None
    threads = payload.get("threads") if isinstance(payload, dict) else None
    items = threads.get("threads") if isinstance(threads, dict) else None
    return [(str(t["id"]), t["name"]) for t in items or []
            if isinstance(t, dict) and isinstance(t.get("id"), (str, int)) and isinstance(t.get("name"), str)]


def reply_text(stdout: str) -> str:
    """The model's reply text from `openclaw agent exec --json` (top-level `payloads[].text`); raise OpenClawError when there is none."""
    out = _json(stdout)
    payloads = out.get("payloads") if isinstance(out, dict) else None
    if isinstance(payloads, list):
        texts = [p["text"] for p in payloads if isinstance(p, dict) and isinstance(p.get("text"), str) and p["text"]]
        if texts:
            return "\n\n".join(texts)
    head = stdout.strip().splitlines()[0][:200] if stdout.strip() else "empty output"
    raise OpenClawError(f"no reply text in the agent output: {head}")
