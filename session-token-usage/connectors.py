"""Read-only scan of Copilot session logs for MCP servers, plugins and skills: what is loaded vs. used (JSON on stdout)."""
import glob
import json
import os
import re
import sys

HOME = os.path.join(os.path.expanduser("~"), ".copilot")
DEFERRED_NAME_TOKENS = 12  # rough prompt cost of one deferred tool name in the tool listing


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            text = re.sub(r"^\s*//.*$", "", f.read(), flags=re.M)
        return json.loads(text)
    except (OSError, ValueError):
        return None


def frontmatter(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(4000)
    except OSError:
        return None, ""
    m = re.match(r"---\s*\n(.*?)\n---", head, re.S)
    if not m:
        return None, ""
    name = re.search(r"^name:\s*(.+)$", m.group(1), re.M)
    desc = re.search(r"^description:\s*(.+?)(?=^\w[\w-]*:|\Z)", m.group(1), re.M | re.S)
    clean = lambda s: s.strip().strip("\"'>|- \n") if s else ""
    return clean(name.group(1)) if name else None, clean(desc.group(1)) if desc else ""


def plugin_of(path):
    parts = os.path.relpath(path, os.path.join(HOME, "installed-plugins")).split(os.sep)
    return parts[1] if len(parts) > 2 else parts[0]


def catalog():
    plugins, skills, server_plugin = {}, {}, {}
    settings = read_json(os.path.join(HOME, "settings.json")) or {}
    for key, on in (settings.get("enabledPlugins") or {}).items():
        name = key.split("@")[0]
        plugins[name] = {"name": name, "enabled": bool(on), "skills": 0, "skillTokens": 0, "servers": []}
    root = os.path.join(HOME, "installed-plugins")
    for path in glob.glob(os.path.join(root, "**", "SKILL.md"), recursive=True):
        rel = os.path.relpath(path, root)
        if any(p.startswith(".") for p in rel.split(os.sep)[:-1]):
            continue
        name, desc = frontmatter(path)
        if not name:
            continue
        plugin = plugin_of(path)
        tokens = (len(name) + len(desc) + 30) // 4
        skills.setdefault(name, {"plugin": plugin, "tokens": tokens})
    for path in glob.glob(os.path.join(HOME, "skills", "*", "SKILL.md")):
        name, desc = frontmatter(path)
        if name:
            skills.setdefault(name, {"plugin": "(user skills)", "tokens": (len(name) + len(desc) + 30) // 4})
    for path in glob.glob(os.path.join(root, "**", ".mcp.json"), recursive=True):
        cfg = read_json(path) or {}
        for srv in (cfg.get("mcpServers") or cfg).keys():
            server_plugin[srv] = plugin_of(path)
    for s in skills.values():
        p = plugins.setdefault(s["plugin"], {"name": s["plugin"], "enabled": True, "skills": 0, "skillTokens": 0, "servers": []})
        p["skills"] += 1
        p["skillTokens"] += s["tokens"]
    for srv, p in server_plugin.items():
        if p in plugins:
            plugins[p]["servers"].append(srv)
    return plugins, skills, server_plugin


def scan_session(path, known):
    """One pass over a session's events.jsonl. Returns None when it has no relevant events."""
    baseline, calls, tool_tokens, price_sum = None, 0, 0, 0.0
    mcp, skills, agents, builtin, loaded = {}, {}, {}, 0, None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith('{"type":"session.tool_baseline"'):
                baseline = json.loads(line)["data"].get("tools") or baseline
            elif line.startswith('{"type":"system.message"'):
                content = json.loads(line)["data"].get("content")
                if isinstance(content, str) and "<available_skills>" in content:
                    loaded = loaded_skills(content)
            elif line.startswith('{"type":"session.usage_record"'):
                u = json.loads(line)["data"].get("usage") or {}
                if u.get("interactionType") == "conversation-agent" or "toolTokenCount" in u:
                    calls += 1
                    tool_tokens += u.get("toolTokenCount") or 0
                    price_sum += input_price(u)
            elif line.startswith('{"type":"tool.execution_start"'):
                d = json.loads(line)["data"]
                srv = d.get("mcpServerName")
                if srv:
                    known.add(srv)
                    t = mcp.setdefault(srv, {})
                    t[d.get("mcpToolName") or d.get("toolName")] = t.get(d.get("mcpToolName") or d.get("toolName"), 0) + 1
                    continue
                builtin += 1
                args = d.get("arguments") or {}
                if d.get("toolName") == "skill" and args.get("skill"):
                    n = str(args["skill"]).split(":")[-1]
                    skills[n] = skills.get(n, 0) + 1
                elif d.get("toolName") == "task" and ":" in str(args.get("agent_type", "")):
                    p = str(args["agent_type"]).split(":")[0]
                    agents[p] = agents.get(p, 0) + 1
    if baseline is None and not mcp and not skills and not calls:
        return None
    return {"baseline": baseline or [], "calls": calls, "toolTokens": tool_tokens, "priceSum": price_sum,
            "loaded": loaded or {}, "mcp": mcp, "skills": skills, "agents": agents, "builtinCalls": builtin}


def loaded_skills(content):
    """Skills actually sent in the system prompt: name -> approx. tokens (full entries and name-only list)."""
    out = {}
    block = content.split("<available_skills>", 1)[1].split("</available_skills>", 1)
    for m in re.finditer(r"<skill>(.*?)</skill>", block[0], re.S):
        n = re.search(r"<name>(.*?)</name>", m.group(1), re.S)
        if n:
            out[n.group(1).strip()] = len(m.group(0)) // 4
    more = re.search(r"Additional skills available[^:]*:\s*([^\n<]+)", block[1] if len(block) > 1 else "")
    for n in (more.group(1).split(",") if more else []):
        if n.strip():
            out.setdefault(n.strip(), len(n) // 4 + 1)
    return out


def input_price(u):
    """Average nano-AIU per input-side token of one model call (input, cache read and cache write)."""
    details = (u.get("copilotUsage") or {}).get("tokenDetails") or []
    nano = sum(d["tokenCount"] * d["costPerBatch"] / d["batchSize"] for d in details
               if d.get("tokenType") != "output" and d.get("batchSize"))
    tokens = sum(d["tokenCount"] for d in details if d.get("tokenType") != "output")
    return nano / tokens if tokens else 0.0


def server_of(name, servers):
    for s in servers:
        if name.startswith(s + "-"):
            return s, name[len(s) + 1:]
    return None, name


def main():
    plugins, skills, server_plugin = catalog()
    known = set(server_plugin) | {"github-mcp-server"}
    raw = {}
    for path in glob.glob(os.path.join(HOME, "session-state", "*", "events.jsonl")):
        sid = os.path.basename(os.path.dirname(path))
        try:
            r = scan_session(path, known)
        except (OSError, ValueError):
            continue
        if r:
            raw[sid] = r
    servers = sorted(known, key=len, reverse=True)
    out = {}
    for sid, r in raw.items():
        per = {}
        total_full = 0
        for t in r["baseline"]:
            srv, _ = server_of(t["name"], servers)
            if srv is None and t.get("deferred") and "-" in t["name"]:
                srv = t["name"].split("-")[0] + " (inferred)"
            if srv is None:
                total_full += 0 if t.get("deferred") else 1
                continue
            e = per.setdefault(srv, {"full": 0, "deferred": 0})
            e["deferred" if t.get("deferred") else "full"] += 1
            total_full += 0 if t.get("deferred") else 1
        per_call = r["toolTokens"] / r["calls"] if r["calls"] else 0
        conn = {}
        for srv, e in per.items():
            share = per_call * e["full"] / total_full if total_full else 0
            conn[srv] = {"full": e["full"], "deferred": e["deferred"],
                         "perCall": round(share + e["deferred"] * DEFERRED_NAME_TOKENS)}
        plug = {}
        for name, tokens in r["loaded"].items():
            p = skills[name]["plugin"] if name in skills else (name.split(":")[0] if ":" in name else "(other)")
            plug[p] = plug.get(p, 0) + tokens
        out[sid] = {"calls": r["calls"], "toolTokens": r["toolTokens"], "priceSum": r["priceSum"],
                    "servers": conn, "plugins": plug,
                    "mcp": r["mcp"], "skills": r["skills"], "agents": r["agents"]}
    json.dump({
        "sessions": out,
        "plugins": list(plugins.values()),
        "skills": {k: v["plugin"] for k, v in skills.items()},
        "serverPlugin": server_plugin,
    }, sys.stdout)


main()
