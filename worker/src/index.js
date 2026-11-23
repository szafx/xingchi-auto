/**
 * 星驰公益站 每日签到 / 转盘 / 盲盒 —— Cloudflare Worker
 *
 * 定时：每天 UTC 16:05（北京 00:05）跑一次；也可以 HTTP 手动触发。
 * 令牌：access + refresh 存在 KV 里（refresh 是旋转式的，每次刷新都要回写）。
 *
 * 环境变量（wrangler secret put）：
 *   XC_SESSION   首次的 access token
 *   XC_REFRESH   首次的 refresh token
 *   XC_API_KEY   sk- 开头的中转 Key（只为查余额）
 *   STATUS_TOKEN 访问 /status、/run 的口令
 * 绑定：STATE（KV）
 */

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj, null, 1), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });

const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36";

async function api(env, path, { method = "GET", token, body, base } = {}) {
  const url = (base || env.CONSOLE_BASE) + path;
  const headers = { Accept: "application/json", "User-Agent": UA };
  if (token) headers["Authorization"] = "Bearer " + token;
  let payload;
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  try {
    const res = await fetch(url, { method, headers, body: payload });
    const text = await res.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text.slice(0, 500) };
    }
    return { status: res.status, data };
  } catch (e) {
    return { status: 0, data: { error: String(e) } };
  }
}

async function getTokens(env) {
  const stored = await env.STATE.get("tokens", "json");
  if (stored && stored.access) return stored;
  return { access: env.XC_SESSION || "", refresh: env.XC_REFRESH || "" };
}

const saveTokens = (env, t) => env.STATE.put("tokens", JSON.stringify(t));

async function runAll(env, trigger) {
  const started = new Date().toISOString();
  const steps = [];
  const draws = [];
  const boxes = [];
  let tokens = await getTokens(env);
  let access = tokens.access;

  // 1) 刷新令牌
  if (tokens.refresh) {
    const r = await api(env, "/auth/refresh", {
      method: "POST",
      token: access,
      body: { refresh_token: tokens.refresh },
    });
    if (r.status === 200 && r.data && r.data.data && r.data.data.access_token) {
      access = r.data.data.access_token;
      const newRefresh = r.data.data.refresh_token || tokens.refresh;
      await saveTokens(env, { access, refresh: newRefresh });
      steps.push({ name: "refresh", status: 200, expires_in: r.data.data.expires_in });
    } else {
      steps.push({ name: "refresh", status: r.status, detail: r.data });
    }
  }

  if (!access) {
    const out = { ok: false, trigger, started, error: "no access token" };
    await env.STATE.put("last_run", JSON.stringify(out));
    return out;
  }

  // 2) 签到
  const cs = await api(env, "/user/checkin", { token: access });
  const cinfo = (cs.data && cs.data.data) || {};
  steps.push({ name: "checkin.status", status: cs.status, detail: cinfo });

  if (cs.status === 200 && cinfo.enabled && !cinfo.claimed_today) {
    const c = await api(env, "/user/checkin", { method: "POST", token: access, body: {} });
    steps.push({ name: "checkin.claim", status: c.status, detail: c.data });
  } else if (cinfo.claimed_today) {
    steps.push({ name: "checkin.claim", status: 0, detail: { skipped: "already claimed", next: cinfo.next_claim_at } });
  }

  // 3) 转盘
  const ls = await api(env, "/lottery", { token: access });
  const lot = (ls.data && ls.data.data) || {};
  steps.push({
    name: "lottery.status",
    status: ls.status,
    detail: {
      available_tickets: lot.available_tickets,
      daily_limit: lot.daily_limit,
      draws_today: lot.draws_today,
      consume_per_ticket: lot.consume_per_ticket,
      reset_at: lot.reset_at,
    },
  });

  if (ls.status === 200 && lot.enabled) {
    let tickets = Number(lot.available_tickets || 0);
    const cap = Number(env.MAX_DRAWS || 20);
    for (let i = 0; i < Math.min(tickets, cap); i++) {
      const d = await api(env, "/lottery/draw", { method: "POST", token: access, body: {} });
      draws.push({ status: d.status, data: d.data });
      if (d.status !== 200) break;
    }
  }

  // 4) 盲盒
  const bs = await api(env, "/lottery/blind-box", { token: access });
  const box = (bs.data && bs.data.data) || {};
  steps.push({ name: "blindbox.status", status: bs.status, detail: box });

  const boxCount = Number(box.available_blind_boxes || 0);
  for (let i = 0; i < Math.min(boxCount, 10); i++) {
    const o = await api(env, "/lottery/blind-box/open", { method: "POST", token: access, body: {} });
    boxes.push({ status: o.status, data: o.data });
    if (o.status !== 200) break;
  }

  // 5) 福利
  const ws = await api(env, "/user/welfare", { token: access });
  steps.push({ name: "welfare", status: ws.status, detail: ws.data && ws.data.data });

  // 6) 余额（走中转 Key，不依赖控制台令牌）
  let balance = null;
  if (env.XC_API_KEY) {
    const us = await api(env, "/usage", { token: env.XC_API_KEY, base: env.RELAY_BASE });
    if (us.status === 200) {
      balance = us.data.remaining ?? us.data.balance ?? null;
      steps.push({ name: "balance", status: 200, detail: { balance, unit: us.data.unit } });
    } else {
      steps.push({ name: "balance", status: us.status, detail: us.data });
    }
  }

  const out = {
    ok: true,
    trigger,
    started,
    finished: new Date().toISOString(),
    balance,
    checked_in: steps.some((s) => s.name === "checkin.claim" && s.status === 200),
    draws,
    boxes,
    steps,
  };
  await env.STATE.put("last_run", JSON.stringify(out));
  return out;
}

function authorized(request, env) {
  if (!env.STATUS_TOKEN) return true;
  const url = new URL(request.url);
  const q = url.searchParams.get("token");
  const h = request.headers.get("x-status-token");
  return q === env.STATUS_TOKEN || h === env.STATUS_TOKEN;
}

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(runAll(env, "cron"));
  },

  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === "/") {
      return json({ ok: true, worker: "xingchi-daily", paths: ["/status", "/run", "/balance"] });
    }

    if (url.pathname === "/balance") {
      if (!env.XC_API_KEY) return json({ ok: false, error: "XC_API_KEY not set" }, 500);
      const r = await api(env, "/usage", { token: env.XC_API_KEY, base: env.RELAY_BASE });
      return json({ ok: r.status === 200, status: r.status, data: r.data });
    }

    if (!authorized(request, env)) return json({ ok: false, error: "unauthorized" }, 401);

    if (url.pathname === "/status") {
      const last = await env.STATE.get("last_run", "json");
      return json({ ok: true, last });
    }

    if (url.pathname === "/run") {
      const out = await runAll(env, "manual");
      return json(out);
    }

    return json({ ok: false, error: "not found" }, 404);
  },
};
