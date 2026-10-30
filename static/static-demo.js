/* Static demo shim - loaded only on the GitHub Pages build (scripts/build_pages.py), never by the live server.
 * It intercepts fetch("/api/...") and answers from JSON recorded from a real run, so app.js runs unchanged.
 * Inputs that were not recorded get an honest "run it locally" error instead of an invented answer. */
(() => {
  const BASE = "api/";
  const LIVE = "This static demo replays a recorded run. Run Sentinel locally for this: python -m sentinel.cli serve";
  const realFetch = window.fetch.bind(window);
  const cache = {};
  const caseState = {}; // case_id -> "investigated" | "approved", advanced by the POSTs below
  let streamPos = 0;

  const load = (rel) => (cache[rel] ??= realFetch(BASE + rel + ".json").then((r) => {
    if (!r.ok) throw new Error(`not recorded: ${rel}`);
    return r.json();
  }));
  const reply = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  const fail = (detail, status = 501) => reply({ detail }, status);
  const recorded = (r) => reply(r.body, r.status);

  async function handle(path, method, body) {
    const route = path.replace(/^\/api\//, "");
    if (method === "GET") {
      if (route === "stream/next") {
        const batches = await load("stream");
        return reply({ items: batches[streamPos++ % batches.length] });
      }
      const c = route.match(/^cases\/([^/]+)$/);
      if (c) {
        const s = await load(`cases/${c[1]}`);
        return reply(s[caseState[c[1]]] || s.before);
      }
      return reply(await load(route));
    }
    const c = route.match(/^cases\/([^/]+)\/(investigate|decision)$/);
    if (c) {
      const s = await load(`cases/${c[1]}`);
      if (c[2] === "investigate") {
        if (!s.investigated) return fail(LIVE);
        caseState[c[1]] = "investigated";
        return reply(s.investigated.state);
      }
      if (body.decision !== "approve" || !s.approved) return fail(`Only "Approve draft" was recorded. ${LIVE}`);
      caseState[c[1]] = "approved";
      return reply(s.approved.state);
    }
    if (route === "copilot/ask") {
      const r = (await load("copilot/answers"))[`${body.mode}|${body.question}`];
      return r ? recorded(r) : fail(`Only the example questions were recorded. ${LIVE}`);
    }
    if (route === "nlp/analyze") {
      const r = (await load("nlp/analyze"))[body.text];
      return r ? recorded(r) : fail(`Only the sample complaints were recorded. ${LIVE}`);
    }
    if (route === "sql") {
      const r = (await load("sql/results"))[body.sql];
      return r ? recorded(r) : fail(`Only the example queries were recorded. ${LIVE}`);
    }
    return fail(LIVE);
  }

  window.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;
    const { pathname } = new URL(url, location.origin);
    if (!pathname.startsWith("/api/")) return realFetch(input, init);
    try {
      return await handle(pathname, (init.method || "GET").toUpperCase(), init.body ? JSON.parse(init.body) : {});
    } catch (e) {
      return fail(e.message, 404);
    }
  };

  document.addEventListener("DOMContentLoaded", () => {
    const b = document.createElement("div");
    b.className = "demo-banner";
    b.innerHTML = '<b>Static demo</b> - a recorded run (offline LLM). Example questions, queries and cases work. ' +
      '<a href="https://github.com/Ravikiran-Kallepally/Sentinel" target="_blank" rel="noopener">Run locally</a> for the rest.';
    document.querySelector(".sidebar")?.append(b);
  });
})();
