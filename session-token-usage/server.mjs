// Local HTTP server for one session-token-usage canvas panel (127.0.0.1, secret path prefix).
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { execFile } from "node:child_process";
import { randomBytes, timingSafeEqual } from "node:crypto";

const UI_HTML = readFileSync(new URL("./ui.html", import.meta.url), "utf8");
const PY = fileURLToPathSafe(new URL("./usage.py", import.meta.url));
const HEADERS = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer" };
const CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'";

function fileURLToPathSafe(u) {
    return decodeURIComponent(u.pathname).replace(/^\/([a-z]:)/i, "$1");
}

export function loadUsage() {
    return new Promise((resolve, reject) => {
        const run = (cmd, cb) => execFile(cmd, [PY], { maxBuffer: 64 * 1024 * 1024, windowsHide: true }, cb);
        run("python", (err, out) => {
            if (err) return run("py", (err2, out2) => (err2 ? reject(err) : resolve(JSON.parse(out2))));
            resolve(JSON.parse(out));
        });
    });
}

export async function startPanelServer(state, onAsk, getQuota) {
    const prefix = `/${randomBytes(24).toString("base64url")}/`;
    let origin = "";
    const authorized = (req, url) => {
        if (!origin || req.headers.host !== new URL(origin).host) return false;
        const got = Buffer.from(url.pathname.slice(0, prefix.length)), want = Buffer.from(prefix);
        return got.length === want.length && timingSafeEqual(got, want);
    };
    const server = createServer(async (req, res) => {
        let url;
        try { url = new URL(req.url, "http://127.0.0.1"); } catch { res.writeHead(400, HEADERS); return res.end(); }
        if (!authorized(req, url) || (req.method !== "GET" && req.method !== "POST")) { res.writeHead(403, HEADERS); return res.end("Forbidden"); }
        const route = url.pathname.slice(prefix.length);
        if (req.method === "POST") {
            const json = (code, body) => { res.writeHead(code, { "Content-Type": "application/json; charset=utf-8", ...HEADERS }); res.end(JSON.stringify(body)); };
            if (route !== "api/ask" || !onAsk || (req.headers.origin && req.headers.origin !== origin)) return json(403, { error: "forbidden" });
            let body = "";
            for await (const chunk of req) { body += chunk; if (body.length > 200000) return json(413, { error: "too large" }); }
            try { await onAsk(body); return json(200, { ok: true }); } catch (e) { return json(500, { error: String(e.message || e) }); }
        }
        if (route === "") {
            res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Content-Security-Policy": CSP, ...HEADERS });
            return res.end(UI_HTML);
        }
        const json = (code, body) => { res.writeHead(code, { "Content-Type": "application/json; charset=utf-8", ...HEADERS }); res.end(JSON.stringify(body)); };
        if (route === "api/data") {
            try { return json(200, { ...(await loadUsage()), selected: state.selected }); }
            catch (e) { return json(500, { error: String(e.message || e) }); }
        }
        if (route === "api/quota") {
            try { return json(200, await getQuota()); }
            catch (e) { return json(500, { error: String(e.message || e) }); }
        }
        if (route === "api/select") { state.selected = url.searchParams.get("id") || null; return json(200, { ok: true }); }
        json(404, { error: "not found" });
    });
    await new Promise((r) => server.listen(0, "127.0.0.1", r));
    origin = `http://127.0.0.1:${server.address().port}`;
    return { url: `${origin}${prefix}`, close: () => new Promise((r) => server.close(r)) };
}
