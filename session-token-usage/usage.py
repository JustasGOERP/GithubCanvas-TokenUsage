"""Read-only dump of Copilot sessions, parent/child links and token usage as JSON (stdout)."""
import json
import os
import sqlite3
import sys

HOME = os.path.join(os.path.expanduser("~"), ".copilot")


def ro(name):
    path = os.path.join(HOME, name).replace("\\", "/")
    if not os.path.exists(path):
        return None
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def rows(c, sql):
    try:
        return [dict(r) for r in c.execute(sql)]
    except sqlite3.Error:
        return []


def main():
    data = ro("data.db")
    if data is None:
        json.dump({"error": "data.db not found in " + HOME}, sys.stdout)
        return
    store = ro("session-store.db")

    projects = {p["id"]: p for p in rows(data, "select id, name, main_repo_path from projects")}
    workspaces = {w["id"]: w for w in rows(
        data, "select id, project_id, name, branch, session_id, archived_at from workspaces")}
    aliases = rows(data, "select session_id, workspace_id from workspace_session_aliases")
    ws_parent = {r["child_workspace_id"]: r["parent_workspace_id"]
                 for r in rows(data, "select child_workspace_id, parent_workspace_id from workspace_parent_links")}
    chat_parent = {r["session_id"]: r["parent_session_id"]
                   for r in rows(data, "select session_id, parent_session_id from chat_parent_links")
                   if r["parent_session_id"]}
    chat_parent.update({r["session_id"]: r["parent_session_id"]
                        for r in rows(data, "select session_id, parent_session_id from session_side_chats")})

    session_ws = {a["session_id"]: a["workspace_id"] for a in aliases}
    for w in workspaces.values():
        if w["session_id"]:
            session_ws[w["session_id"]] = w["id"]

    models = {}
    if store is not None:
        for r in map(dict, store.execute("""
            select session_id, model, substr(created_at,1,7) ym, count(*) calls,
                   coalesce(sum(input_tokens),0) input, coalesce(sum(output_tokens),0) output,
                   coalesce(sum(cache_read_tokens),0) cache_read, coalesce(sum(cache_write_tokens),0) cache_write,
                   coalesce(sum(reasoning_tokens),0) reasoning,
                   count(total_nano_aiu) cost_calls, coalesce(sum(total_nano_aiu),0) nano_aiu
            from assistant_usage_events group by session_id, model, ym""")):
            models.setdefault(r["session_id"], []).append(r)

    months = {}
    if store is not None:
        for r in rows(store, """
            select session_id, substr(created_at,1,7) ym,
                   coalesce(sum(input_tokens),0) input, coalesce(sum(output_tokens),0) output,
                   coalesce(sum(cache_read_tokens),0) cached, coalesce(sum(reasoning_tokens),0) reasoning
            from assistant_usage_events group by session_id, ym"""):
            months.setdefault(r["session_id"], {})[r["ym"]] = {
                k: r[k] for k in ("input", "output", "cached", "reasoning")}

    sessions = {s["id"]: s for s in rows(data, """
        select id, title, created_at, updated_at, model, archived_at,
               coalesce(total_input_tokens,0) input, coalesce(total_output_tokens,0) output,
               coalesce(total_cached_tokens,0) cached, coalesce(total_reasoning_tokens,0) reasoning
        from sessions""")}

    out = []
    for sid, s in sessions.items():
        wid = session_ws.get(sid)
        w = workspaces.get(wid) if wid else None
        parent = chat_parent.get(sid)
        if not parent and w:
            pw = workspaces.get(ws_parent.get(wid))
            if pw and pw["session_id"] and pw["session_id"] != sid:
                parent = pw["session_id"]
            elif w["session_id"] and w["session_id"] != sid:
                parent = w["session_id"]
        if parent == sid or parent not in sessions:
            parent = None
        proj = projects.get(w["project_id"]) if w else None
        out.append({
            "id": sid,
            "title": s["title"] or (w["name"] if w else None) or sid[:8],
            "created": s["created_at"], "updated": s["updated_at"],
            "archived": bool(s["archived_at"] or (w and w["archived_at"])),
            "parent": parent,
            "project": proj["name"] if proj else None,
            "folder": proj["main_repo_path"] if proj else None,
            "branch": w["branch"] if w else None,
            "input": s["input"], "output": s["output"], "cached": s["cached"], "reasoning": s["reasoning"],
            "models": models.get(sid, []),
            "months": months.get(sid, {}),
        })
    json.dump({"sessions": out}, sys.stdout)


main()
