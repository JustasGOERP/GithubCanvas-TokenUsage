// Extension: session-token-usage
// Canvas that shows token usage per Copilot session, rolled up with all of its sub-sessions.
import { joinSession, createCanvas } from "@github/copilot-sdk/extension";
import { startPanelServer, loadUsage } from "./server.mjs";

const panels = new Map(); // instanceId -> { server, state }

function rollUp(sessions, rootId) {
    const byId = new Map(sessions.map((s) => [s.id, s]));
    const kids = new Map();
    for (const s of sessions) if (s.parent) (kids.get(s.parent) ?? kids.set(s.parent, []).get(s.parent)).push(s);
    const seen = new Set();
    const walk = (s) => {
        seen.add(s.id);
        const children = (kids.get(s.id) ?? []).filter((c) => !seen.has(c.id)).map(walk);
        const own = { input: s.input, output: s.output, cached: s.cached, reasoning: s.reasoning };
        const total = { ...own };
        for (const c of children) for (const k in total) total[k] += c.total[k];
        return { id: s.id, title: s.title, archived: s.archived, own, total, children };
    };
    const root = byId.get(rootId);
    if (!root) throw new Error(`Session ${rootId} not found`);
    return walk(root);
}

const session = await joinSession({
    canvases: [
        createCanvas({
            id: "session-token-usage",
            displayName: "Session Token Usage",
            description: "Pick a session and see how many tokens it and all of its sub-sessions used (input, cached, output, reasoning, per model).",
            inputSchema: {
                type: "object",
                properties: { sessionId: { type: "string", description: "Session to preselect." } },
            },
            actions: [
                {
                    name: "getUsage",
                    description: "Token usage of a session including all sub-sessions. Defaults to the session selected in the panel.",
                    inputSchema: { type: "object", properties: { sessionId: { type: "string" } } },
                    handler: async (ctx) => {
                        const id = ctx.input?.sessionId || panels.get(ctx.instanceId)?.state.selected;
                        if (!id) throw new Error("No session given or selected.");
                        return rollUp((await loadUsage()).sessions, id);
                    },
                },
            ],
            open: async (ctx) => {
                let panel = panels.get(ctx.instanceId);
                const preselect = ctx.input && typeof ctx.input === "object" ? ctx.input.sessionId : null;
                if (!panel) {
                    const state = { selected: preselect || null };
                    panel = { state, server: await startPanelServer(state, (text) => session.send({ prompt: text }), () => session.rpc.quota.refresh()) };
                    panels.set(ctx.instanceId, panel);
                } else if (preselect) panel.state.selected = preselect;
                return { title: "Session Token Usage", url: panel.server.url };
            },
            onClose: async (ctx) => {
                const panel = panels.get(ctx.instanceId);
                if (panel) { panels.delete(ctx.instanceId); await panel.server.close(); }
            },
        }),
    ],
});
