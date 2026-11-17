"""Draft (or revise) a script with Claude, then check it and let Claude fix
what the check finds, a few rounds at most.

Optional: without an API key everything else works, and a script can be
written or uploaded by hand. The key comes from ANTHROPIC_API_KEY, or the
Settings page (kept in data/settings.json on the server only).
"""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path

from . import config, jobs, store

MODEL = "claude-opus-5-5"
# $ per million tokens (Claude Opus 5.5): input, output, cache write, cache read
PRICE = {"claude-opus-5-5": (4.0, 20.0, 5.0, 0.20), "claude-sonnet-5-5": (2.0, 10.0, 2.5, 0.10)}
TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml", ".py", ".js", ".mjs", ".ts", ".tsx",
            ".jsx", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".cs", ".c", ".h", ".cpp", ".sql", ".sh", ".html",
            ".css", ".xml", ".toml", ".ini", ".cfg", ".log", ".rst", ".tex"}
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_TEXT_CHARS = 2_000_000          # about 500K tokens: more than that is refused, never cut
SCHEMA = {
    "type": "object",
    "properties": {
        "scenes_js": {"type": "string", "description": "The whole scenes.js file"},
        "description": {"type": "string", "description": "One sentence: what the explainer shows"},
        "say": {"type": "array", "items": {"type": "array", "items": {"type": "string"}},
                "description": "[spelling, how to say it] pairs for the voice, e.g. [\"P&L\", \"P and L\"]"},
        "notes": {"type": "string", "description": "Anything the author should know: what's estimated, what was left out"},
    },
    "required": ["scenes_js", "description", "say", "notes"],
    "additionalProperties": False,
}


def settings() -> dict:
    try:
        return json.loads(config.SETTINGS.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def save_settings(patch: dict) -> dict:
    s = settings()
    if "anthropic_api_key" in patch:
        k = (patch["anthropic_api_key"] or "").strip()
        if k:
            s["anthropic_api_key"] = k
        else:
            s.pop("anthropic_api_key", None)
    if "model" in patch:
        if patch["model"] not in PRICE:
            raise ValueError(f"Model is one of {', '.join(PRICE)}")
        s["model"] = patch["model"]
    if "rounds" in patch:
        s["rounds"] = max(0, min(5, int(patch["rounds"])))
    config.DATA.mkdir(parents=True, exist_ok=True)
    config.SETTINGS.write_text(json.dumps(s))
    try:
        config.SETTINGS.chmod(0o600)
    except OSError:
        pass
    return public_settings()


def api_key() -> str | None:
    import os
    return os.environ.get("ANTHROPIC_API_KEY") or settings().get("anthropic_api_key")


def public_settings() -> dict:
    import os
    s = settings()
    return {"has_key": bool(api_key()), "key_from_env": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "model": s.get("model", MODEL), "models": list(PRICE), "rounds": s.get("rounds", 2)}


def system_prompt() -> str:
    guide = (config.KIT / "WRITING.md").read_text()
    example = (config.KIT / "example" / "scenes.js").read_text()
    return (
        "You write narrated, animated explainers for Explainer Studio. Your output is a scenes.js file for the "
        "engine described below, plus the description and pronunciations. The person gives you a brief and "
        "their material; everything on screen and in the voice must come from that material, and anything "
        "estimated must be said to be estimated.\n\n"
        "Write scenes.js only: no HTML, no build steps, no imports. Use only the helpers the guide lists. The "
        "tools mentioned in the guide (steps.mjs, build.py, check.mjs, render.mjs) are run for you; the check's "
        "findings may be sent back to you to fix, and then you return the whole corrected file.\n\n"
        "<writing_guide>\n" + guide + "\n</writing_guide>\n\n"
        "<example_scenes_js>\n" + example + "\n</example_scenes_js>"
    )


def material(pid: str) -> tuple[list, list[str]]:
    """The uploads as content blocks: text inline, PDFs and images as
    documents and images. Too much text is refused, not cut."""
    blocks, notes, chars = [], [], 0
    for u in store.uploads(pid):
        f = store.root(pid) / "uploads" / u["name"]
        mt = mimetypes.guess_type(f.name)[0] or ""
        if f.suffix.lower() == ".pdf":
            blocks.append({"type": "document", "title": f.name,
                           "source": {"type": "base64", "media_type": "application/pdf",
                                      "data": base64.standard_b64encode(f.read_bytes()).decode()}})
        elif mt in IMAGE_TYPES:
            blocks.append({"type": "text", "text": f"Image: {f.name}"})
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": mt,
                                                         "data": base64.standard_b64encode(f.read_bytes()).decode()}})
        elif f.suffix.lower() in TEXT_EXT or mt.startswith("text/"):
            text = f.read_text(errors="replace")
            chars += len(text)
            blocks.append({"type": "text", "text": f"<file name=\"{f.name}\">\n{text}\n</file>"})
        else:
            notes.append(f"{f.name}: not a kind of file Claude reads (text, code, PDF or image), left out")
    if chars > MAX_TEXT_CHARS:
        raise jobs.Failed(f"The uploads hold {chars:,} characters of text, more than one request can take "
                          f"({MAX_TEXT_CHARS:,}). Remove some, or split the explainer into parts.")
    return blocks, notes


async def check_script(j: jobs.Job) -> list[str]:
    """What's wrong with the script as it stands: a mistake steps.mjs finds,
    or the check's failures on a page without voice. [] if nothing."""
    try:
        await jobs.outline(j)
        html = await jobs.build(j, voice=False)
    except jobs.Failed as e:
        return [str(e)]
    lines: list[str] = []
    try:
        await j.run(config.NODE, config.KIT / "tools" / "check.mjs", html, "--fast", on_line=lines.append)
        return []
    except jobs.Failed:
        fails = [line.strip() for line in lines if line.strip() and not line.startswith("  step")]
        return fails[-40:] or ["The check failed; see the log"]


async def run_draft(j: jobs.Job) -> None:
    import anthropic

    key = api_key()
    if not key:
        raise jobs.Failed("No Claude API key: add one in Settings, or write or upload the script yourself")
    s = settings()
    model = s.get("model", MODEL)
    rounds = int(j.opts.get("rounds", s.get("rounds", 2)))
    d = store.read(j.pid)
    instruction = (j.opts.get("instruction") or "").strip()
    current = store.script(j.pid)
    revise = bool(instruction and current.strip())
    j.at("Reading your material", 0.05)
    blocks, notes = material(j.pid)
    for n in notes:
        j.log(n)
    ask = [f"Title: {d['title']}", f"Brief: {d.get('brief') or '(none)'}"]
    if revise:
        ask += ["Here is the current scenes.js. Change it as asked below and return the whole file.",
                f"<current_scenes_js>\n{current}\n</current_scenes_js>", f"What to change: {instruction}"]
    else:
        ask += ["Write the explainer from the material above." + (f" Also: {instruction}" if instruction else "")]
    messages = [{"role": "user", "content": blocks + [{"type": "text", "text": "\n\n".join(ask)}]}]
    client = anthropic.AsyncAnthropic(api_key=key)
    system = [{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}]
    usage = {"in": 0, "out": 0, "cache_write": 0, "cache_read": 0}
    result = None
    for attempt in range(rounds + 1):
        j.at("Claude is writing the script" if attempt == 0 else f"Claude is fixing what the check found (round {attempt})",
             0.1 + 0.8 * attempt / (rounds + 1))
        try:
            async with client.beta.messages.stream(
                model=model, max_tokens=64000, system=system, messages=messages,
                output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
                betas=["server-side-fallback-2026-07-01"], extra_body={"fallbacks": "default"},
            ) as stream:
                resp = await stream.get_final_message()
        except anthropic.AuthenticationError:
            raise jobs.Failed("Claude refused the API key: check it in Settings")
        except anthropic.PermissionDeniedError as e:
            raise jobs.Failed(f"The API key can't use {model}: {e.message}")
        except anthropic.RateLimitError:
            raise jobs.Failed("Claude's rate limit was hit: try again in a minute")
        except anthropic.BadRequestError as e:
            raise jobs.Failed(f"Claude couldn't take the request: {e.message}")
        except anthropic.APIStatusError as e:
            raise jobs.Failed(f"Claude's API answered {e.status_code}: {e.message}")
        except anthropic.APIConnectionError:
            raise jobs.Failed("Couldn't reach Claude's API: check the server's network")
        u = resp.usage
        usage["in"] += u.input_tokens or 0
        usage["out"] += u.output_tokens or 0
        usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        if resp.stop_reason == "refusal":
            raise jobs.Failed("Claude declined to write this explainer")
        if resp.stop_reason == "max_tokens":
            raise jobs.Failed("Claude's answer ran over its length limit: ask for fewer or shorter scenes")
        text = "".join(b.text for b in resp.content if b.type == "text")
        try:
            result = json.loads(text)
        except ValueError:
            raise jobs.Failed("Claude's answer wasn't in the expected form; try again")
        store.save_script(j.pid, result["scenes_js"])
        store.update(j.pid, {"description": result.get("description", ""),
                             "settings": {"say": result.get("say") or []}})
        j.log(f"Script saved ({len(result['scenes_js']):,} characters)")
        if result.get("notes"):
            j.log("Claude's notes: " + result["notes"])
        problems = await check_script(j)
        if not problems:
            break
        j.log("The check found: " + " | ".join(problems[:10]))
        if attempt == rounds:
            notes.append("The check still finds problems after the fixing rounds: see the log, then edit or revise")
            break
        messages += [{"role": "assistant", "content": resp.content},
                     {"role": "user", "content": "The check found these problems. Fix them and return the whole "
                                                 "corrected file in the same form:\n" + "\n".join(problems)}]
    pin, pout, pcw, pcr = PRICE[model]
    cost = (usage["in"] * pin + usage["out"] * pout + usage["cache_write"] * pcw + usage["cache_read"] * pcr) / 1e6
    j.result = {"model": model, "usage": usage, "cost_usd": round(cost, 3), "notes": notes + (
        [result["notes"]] if result and result.get("notes") else [])}
    j.log(f"Claude: {usage['in']:,} tokens in, {usage['out']:,} out (+{usage['cache_read']:,} from cache): about ${cost:.2f}")


jobs.RUNNERS["draft"] = run_draft
