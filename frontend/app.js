// Elektrocar – Oberfläche
const $ = (id) => document.getElementById(id);
const RING = 2 * Math.PI * 52;
const state = { data: null, temp: 21, password: null, map: null, marker: null };

try { state.password = localStorage.getItem("pw"); } catch {}
try { state.temp = Number(localStorage.getItem("temp")) || 21; } catch {}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  if (state.password) headers["X-App-Password"] = state.password;
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401) {
    await askPassword();
    return api(path, opts);
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `Fehler ${res.status}`);
  return body;
}

function askPassword() {
  return new Promise((resolve) => {
    const dlg = $("login");
    $("loginForm").onsubmit = () => {
      state.password = $("pw").value;
      try { localStorage.setItem("pw", state.password); } catch {}
      resolve();
    };
    dlg.showModal();
  });
}

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), Math.max(3500, msg.length * 70));
}

const fmt = (n, d = 0) =>
  n == null ? "–" : Number(n).toLocaleString("de-AT", { maximumFractionDigits: d });

function timeAgo(iso) {
  if (!iso) return "unbekannt";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "gerade eben";
  if (s < 3600) return `vor ${Math.round(s / 60)} Min.`;
  if (s < 86400) return `vor ${Math.round(s / 3600)} Std.`;
  return new Date(iso).toLocaleString("de-AT");
}

function render(d) {
  state.data = d;
  $("demo").hidden = !d.demo;
  $("error").hidden = true;
  $("name").textContent = d.name;
  $("model").textContent = [d.model, d.year].filter(Boolean).join(" · ");

  // Akku
  const soc = d.soc ?? 0;
  $("soc").textContent = fmt(d.soc);
  $("ringbar").style.strokeDashoffset = RING * (1 - soc / 100);
  document.querySelector(".ring-wrap").classList.toggle("low", soc < 20);
  const cs = $("chargeState");
  cs.className = "charge-state" + (d.charging ? " charging" : "");
  cs.textContent = d.charging ? "Lädt" : d.plugged_in ? "Angesteckt" : "Nicht angesteckt";

  $("range").textContent = fmt(d.range_km);
  $("rangeUnit").textContent = d.range_unit;
  $("odo").textContent = fmt(d.odometer);
  $("odoUnit").textContent = d.odometer_unit;
  $("locks").textContent =
    d.doors_locked == null ? "–" : d.doors_locked ? "🔒 Zu" : "🔓 Offen";

  // Klima
  if (d.climate_temp && !renderClimate.touched) state.temp = d.climate_temp;
  renderClimate();

  // Aktionen nur, wenn das Auto sie unterstützt
  document.querySelectorAll(".action").forEach((b) => {
    const cmd = b.dataset.cmd;
    const ok = d.supported_commands.includes(cmd) ||
      (cmd === "CNOW" && d.supported_commands.includes("CNOW2"));
    b.disabled = !ok;
    b.title = ok ? "" : "Wird von deinem Auto nicht unterstützt";
  });

  renderMap(d.location);
  $("updated").textContent = `Stand: ${timeAgo(d.updated)}`;
}

function renderClimate() {
  const d = state.data || {};
  const pending = state.climatePending; // "on" | "off" | undefined
  let on = !!d.climate_on;
  let left = d.climate_until ? Math.ceil((d.climate_until * 1000 - Date.now()) / 60000) : null;
  if (left != null && left <= 0) { on = false; left = null; }

  const card = $("climateCard");
  card.classList.toggle("on", on && !pending);
  card.classList.toggle("waiting", !!pending);
  $("climateState").textContent = pending
    ? (pending === "on" ? "Wird eingeschaltet …" : "Wird ausgeschaltet …")
    : on ? (left != null ? `Läuft · noch ca. ${left} Min.` : "Läuft") : "Aus";

  // beide Knöpfe immer da; der passende ist hervorgehoben
  $("climateOn").classList.toggle("on", on && !pending);
  $("climateOn").classList.toggle("pending", pending === "on");
  $("climateOff").classList.toggle("pending", pending === "off");
  $("climateOn").disabled = $("climateOff").disabled = !!pending;
  $("climateHint").textContent = on && d.climate_estimated
    ? "Das Auto meldet keinen Klima-Status, die Restzeit ist ab dem Einschalten geschätzt."
    : "Die Vorklimatisierung läuft ca. 15 Minuten. Der Befehl braucht bis zu einer Minute, bis das Auto bestätigt.";
  $("temp").textContent = fmt(state.temp, 1);
}
setInterval(renderClimate, 30000);

function renderMap(loc) {
  if (!loc) {
    $("locInfo").textContent = "Keine Position verfügbar.";
    return;
  }
  $("navLink").href = `https://www.google.com/maps/dir/?api=1&destination=${loc.lat},${loc.lon}`;
  if (typeof L === "undefined") {
    $("map").textContent = "Karte konnte nicht geladen werden.";
  } else {
    showOnMap(loc);
  }
  $("locInfo").textContent =
    `${loc.lat.toFixed(5)}, ${loc.lon.toFixed(5)}` +
    (loc.approximate ? " (ungefähr)" : "") +
    ` · ${timeAgo(loc.updated)}`;
  reverseGeocode(loc);
}

function showOnMap(loc) {
  const pos = [loc.lat, loc.lon];
  if (!state.map) {
    state.map = L.map("map", { zoomControl: false, attributionControl: true }).setView(pos, 16);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "© OpenStreetMap",
    }).addTo(state.map);
    const icon = L.divIcon({ className: "", html: '<div class="car-marker"></div>', iconSize: [22, 22] });
    state.marker = L.marker(pos, { icon }).addTo(state.map);
  } else {
    state.marker.setLatLng(pos);
    state.map.panTo(pos);
  }
}

async function reverseGeocode(loc) {
  const key = `${loc.lat.toFixed(4)},${loc.lon.toFixed(4)}`;
  if (reverseGeocode.last === key) return;
  reverseGeocode.last = key;
  try {
    const r = await fetch(
      `https://nominatim.openstreetmap.org/reverse?format=json&zoom=18&lat=${loc.lat}&lon=${loc.lon}`,
      { headers: { "Accept-Language": "de" } }
    );
    const j = await r.json();
    const a = j.address || {};
    const street = [a.road, a.house_number].filter(Boolean).join(" ");
    const place = a.city || a.town || a.village || "";
    if (street || place) {
      $("locInfo").textContent = `📍 ${[street, place].filter(Boolean).join(", ")} · ${timeAgo(loc.updated)}`;
    }
  } catch {}
}

async function load(force = false) {
  const btn = $("refresh");
  btn.classList.add("spin");
  try {
    render(await api(`/api/status${force ? "?force=true" : ""}`));
  } catch (e) {
    $("error").textContent = e.message;
    $("error").hidden = false;
  } finally {
    btn.classList.remove("spin");
  }
}

async function waitForJob(job) {
  for (let i = 0; i < 60; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    job = await api(`/api/jobs/${job.id}`);
    if (job.state !== "pending") return job;
  }
  return { ...job, state: "failed", error: "Zeitüberschreitung" };
}

async function runJob(button, request, label) {
  button.classList.add("busy");
  toast(`${label} wird gesendet …`);
  try {
    const job = await waitForJob(await request());
    toast(job.state === "ok" ? `✅ ${label} erledigt` : `⚠️ ${label} fehlgeschlagen${job.error ? ": " + job.error : ""}`);
    if (job.state === "ok") await load(true);
  } catch (e) {
    toast(`⚠️ ${e.message}`);
  } finally {
    button.classList.remove("busy");
  }
}

// Ereignisse
$("refresh").onclick = async () => {
  try {
    await api("/api/wake", { method: "POST" });
    toast("Auto wird aufgeweckt – frische Daten folgen in Kürze.");
  } catch {}
  await load(true);
  setTimeout(() => load(true), 30000);
};

function changeTemp(delta) {
  state.temp = Math.min(28, Math.max(16, state.temp + delta));
  renderClimate.touched = true;
  try { localStorage.setItem("temp", state.temp); } catch {}
  renderClimate();
}
$("tempDown").onclick = () => changeTemp(-0.5);
$("tempUp").onclick = () => changeTemp(0.5);

async function setClimate(on) {
  state.climatePending = on ? "on" : "off";
  renderClimate();
  try {
    await runJob($(on ? "climateOn" : "climateOff"), () =>
      api("/api/climate", { method: "POST", body: JSON.stringify({ on, temperature: state.temp }) }),
      on ? "Klima ein" : "Klima aus");
  } finally {
    state.climatePending = undefined;
    renderClimate();
  }
}
$("climateOn").onclick = () => setClimate(true);
$("climateOff").onclick = () => setClimate(false);

document.querySelectorAll(".action").forEach((b) => {
  b.onclick = () => {
    if (b.dataset.cmd === "RDU" && !confirm("Auto wirklich entriegeln?")) return;
    runJob(b, () => api(`/api/command/${b.dataset.cmd}`, { method: "POST" }), b.textContent.trim());
  };
});

document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
setInterval(() => { if (!document.hidden) load(); }, 60000);

// ------------------------------------------------------------------------
// Verlauf
// ------------------------------------------------------------------------

const NS = "http://www.w3.org/2000/svg";
const WD = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
let historyDays = 7;
try { historyDays = Number(localStorage.getItem("days")) || 7; } catch {}

function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

const dayLabel = (d) => `${WD[d.getDay()]} ${d.getDate()}.${d.getMonth() + 1}.`;
const hm = (d) => d.toLocaleTimeString("de-AT", { hour: "2-digit", minute: "2-digit" });

function duration(sec) {
  const m = Math.round(sec / 60);
  return m < 60 ? `${m} Min.` : `${Math.floor(m / 60)} Std. ${m % 60 ? (m % 60) + " Min." : ""}`.trim();
}

function relDay(d) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const diff = Math.round((today - new Date(d).setHours(0, 0, 0, 0)) / 86400000);
  return diff === 0 ? "Heute" : diff === 1 ? "Gestern" : dayLabel(d);
}

function tooltip(chart, x, html) {
  let tip = chart.querySelector(".tip");
  if (!tip) { tip = document.createElement("div"); tip.className = "tip"; chart.appendChild(tip); }
  tip.innerHTML = html;
  tip.hidden = false;
  const w = chart.clientWidth, half = tip.offsetWidth / 2;
  tip.style.left = `${Math.min(w - half, Math.max(half, x))}px`;
  tip.style.top = `-${tip.offsetHeight + 6}px`;
}
const hideTip = (chart) => { const t = chart.querySelector(".tip"); if (t) t.hidden = true; };

function socChart(points, days) {
  const box = $("socChart");
  box.innerHTML = "";
  const W = box.clientWidth || 320, H = 170, m = { l: 36, r: 6, t: 8, b: 22 };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img",
    "aria-label": "Verlauf des Ladezustands" }, box);
  const now = Date.now() / 1000, t0 = now - days * 86400;
  const x = (t) => m.l + ((t - t0) / (now - t0)) * (W - m.l - m.r);
  const y = (v) => m.t + (1 - v / 100) * (H - m.t - m.b);

  for (const v of [0, 50, 100]) {
    el("line", { class: "gridline", x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }, svg);
    el("text", { class: "axis", x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }, svg).textContent = `${v} %`;
  }
  // Zeitachse
  const ticks = [];
  const d = new Date(t0 * 1000);
  if (days <= 1) {
    d.setMinutes(0, 0, 0); d.setHours(Math.ceil(d.getHours() / 6) * 6);
    for (; d / 1000 <= now; d.setHours(d.getHours() + 6)) ticks.push([d / 1000, hm(d)]);
  } else {
    d.setHours(0, 0, 0, 0); d.setDate(d.getDate() + 1);
    const step = days <= 7 ? 1 : 7;
    for (; d / 1000 <= now; d.setDate(d.getDate() + step))
      ticks.push([d / 1000, step === 1 ? WD[d.getDay()] : `${d.getDate()}.${d.getMonth() + 1}.`]);
  }
  for (const [t, label] of ticks) {
    el("text", { class: "axis", x: x(t), y: H - 6, "text-anchor": "middle" }, svg).textContent = label;
  }

  if (points.length < 2) {
    el("text", { class: "empty", x: W / 2, y: H / 2, "text-anchor": "middle" }, svg).textContent =
      "Noch keine Daten – der Verlauf füllt sich ab jetzt.";
    return;
  }

  // Ladephasen als Hintergrund-Bänder
  for (let i = 0; i < points.length - 1; i++) {
    if (!points[i].charging) continue;
    let j = i;
    while (j < points.length - 1 && points[j].charging) j++;
    el("rect", { class: "band", x: x(points[i].ts), y: m.t, width: Math.max(2, x(points[j].ts) - x(points[i].ts)),
      height: H - m.t - m.b, rx: 3 }, svg);
    i = j;
  }

  // Linie mit Lücken, wenn länger keine Daten kamen
  const GAP = Math.max(3 * 3600, days * 86400 / 40);
  let line = "", area = "", seg = [];
  const flush = () => {
    if (seg.length > 1) {
      const p = seg.map((q, k) => `${k ? "L" : "M"}${x(q.ts).toFixed(1)},${y(q.soc).toFixed(1)}`).join("");
      line += p;
      area += `${p}L${x(seg.at(-1).ts).toFixed(1)},${y(0)}L${x(seg[0].ts).toFixed(1)},${y(0)}Z`;
    }
    seg = [];
  };
  points.forEach((p, i) => {
    if (i && p.ts - points[i - 1].ts > GAP) flush();
    seg.push(p);
  });
  flush();
  el("path", { class: "area", d: area }, svg);
  el("path", { class: "line", d: line }, svg);

  // Hover / Antippen: Fadenkreuz und Tooltip
  const cross = el("line", { class: "cross", y1: m.t, y2: H - m.b, visibility: "hidden" }, svg);
  const dot = el("circle", { class: "dot", r: 5, visibility: "hidden" }, svg);
  const hit = el("rect", { x: m.l, y: 0, width: W - m.l - m.r, height: H, fill: "transparent" }, svg);
  const move = (ev) => {
    const r = svg.getBoundingClientRect();
    const t = t0 + ((ev.clientX - r.left) * (W / r.width) - m.l) / (W - m.l - m.r) * (now - t0);
    let best = points[0];
    for (const p of points) if (Math.abs(p.ts - t) < Math.abs(best.ts - t)) best = p;
    const px = x(best.ts);
    cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", px); dot.setAttribute("cy", y(best.soc)); dot.setAttribute("visibility", "visible");
    const dt = new Date(best.ts * 1000);
    tooltip(box, px * (r.width / W), `${relDay(dt)} ${hm(dt)}<br><b>${fmt(best.soc)} %</b>${best.charging ? " · lädt" : ""}`);
  };
  const leave = (ev) => {
    if (ev.pointerType === "touch") return; // am Handy stehen lassen, bis woanders getippt wird
    cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); hideTip(box);
  };
  box.clear = () => leave({});
  hit.addEventListener("pointermove", move);
  hit.addEventListener("pointerdown", move);
  hit.addEventListener("pointerleave", leave);
}

function kmChart(daily, days) {
  const box = $("kmChart");
  box.innerHTML = "";
  $("kmWrap").hidden = days <= 1;
  if (days <= 1) return;
  const W = box.clientWidth || 320, H = 130, m = { l: 36, r: 6, t: 8, b: 22 };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img",
    "aria-label": "Gefahrene Kilometer pro Tag" }, box);

  // die letzten N Tage, Tage ohne Daten bleiben leer
  const byDay = Object.fromEntries(daily.map((r) => [r.day, r.km]));
  const slots = [];
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(); d.setHours(12, 0, 0, 0); d.setDate(d.getDate() - i);
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    slots.push({ d, km: byDay[key] });
  }
  const max = Math.max(10, ...slots.map((s) => s.km || 0));
  const step = max > 200 ? 100 : max > 100 ? 50 : max > 40 ? 20 : 10;
  const top = Math.ceil(max / step) * step;
  const y = (v) => m.t + (1 - v / top) * (H - m.t - m.b);
  for (let v = 0; v <= top; v += step) {
    el("line", { class: "gridline", x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }, svg);
    el("text", { class: "axis", x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }, svg).textContent = v;
  }
  const slot = (W - m.l - m.r) / slots.length;
  const bw = Math.max(3, Math.min(28, slot - 2));
  const labelEvery = days <= 7 ? 1 : 7;
  slots.forEach((s, i) => {
    const cx = m.l + slot * (i + 0.5);
    if ((slots.length - 1 - i) % labelEvery === 0) {
      el("text", { class: "axis", x: cx, y: H - 6, "text-anchor": "middle" }, svg).textContent =
        days <= 7 ? WD[s.d.getDay()] : `${s.d.getDate()}.${s.d.getMonth() + 1}.`;
    }
    if (s.km == null) return;
    const h = y(0) - y(s.km), r = Math.min(4, bw / 2, h);
    const x0 = cx - bw / 2;
    const bar = el("path", { class: "bar", d: h <= 0 ? "" :
      `M${x0},${y(0)}V${y(s.km) + r}Q${x0},${y(s.km)} ${x0 + r},${y(s.km)}H${x0 + bw - r}Q${x0 + bw},${y(s.km)} ${x0 + bw},${y(s.km) + r}V${y(0)}Z` }, svg);
    const hit = el("rect", { x: cx - slot / 2, y: 0, width: slot, height: H, fill: "transparent" }, svg);
    const show = () => {
      svg.querySelectorAll(".bar.active").forEach((b) => b.classList.remove("active"));
      bar.classList.add("active");
      const r2 = svg.getBoundingClientRect();
      tooltip(box, cx * (r2.width / W), `${relDay(s.d)}<br><b>${fmt(s.km)} km</b>`);
    };
    hit.addEventListener("pointerenter", show);
    hit.addEventListener("pointerdown", show);
    hit.addEventListener("pointerleave", (ev) => {
      if (ev.pointerType === "touch") return;
      bar.classList.remove("active"); hideTip(box);
    });
  });
}

// Antippen außerhalb eines Diagramms schließt dessen Tooltip
document.addEventListener("pointerdown", (ev) => {
  for (const id of ["socChart", "kmChart"]) {
    const box = $(id);
    if (box.contains(ev.target)) continue;
    box.clear?.();
    box.querySelectorAll(".bar.active").forEach((b) => b.classList.remove("active"));
    hideTip(box);
  }
});

function renderSessions(sessions) {
  const ul = $("sessionList");
  ul.innerHTML = "";
  if (!sessions.length) {
    ul.innerHTML = '<li class="none">Keine Ladevorgänge in diesem Zeitraum.</li>';
    return;
  }
  for (const s of sessions.slice(0, 5)) {
    const a = new Date(s.start * 1000), b = new Date(s.end * 1000);
    const gain = (s.soc_end ?? 0) - (s.soc_start ?? 0);
    const li = document.createElement("li");
    li.innerHTML = `
      <span class="when">${relDay(a)}, ${hm(a)}${s.active ? "" : "–" + hm(b)}</span>
      <span class="gain">+${fmt(Math.max(0, gain))} %</span>
      <span class="meta">${s.active ? '<span class="active">⚡ lädt gerade</span> · ' : ""}${duration(s.end - s.start)} · ${fmt(s.soc_start)} → ${fmt(s.soc_end)} %</span>`;
    ul.appendChild(li);
  }
}

async function loadHistory() {
  document.querySelectorAll("#range button").forEach((b) =>
    b.setAttribute("aria-selected", String(Number(b.dataset.days) === historyDays)));
  try {
    const h = await api(`/api/history?days=${historyDays}`);
    const since = Date.now() / 1000 - historyDays * 86400;
    const inRange = h.sessions.filter((s) => s.end >= since);
    $("hDriven").textContent = h.driven_km == null ? "–" : `${fmt(h.driven_km)} km`;
    $("hSessions").textContent = inRange.length;
    $("hCharged").textContent = `${fmt(inRange.reduce((a, s) => a + Math.max(0, (s.soc_end ?? 0) - (s.soc_start ?? 0)), 0))} %`;
    state.history = h;
    socChart(h.points, historyDays);
    kmChart(h.daily_km, historyDays);
    renderSessions(inRange);
  } catch (e) {
    $("socChart").textContent = `Verlauf nicht verfügbar: ${e.message}`;
  }
}

document.querySelectorAll("#range button").forEach((b) => {
  b.onclick = () => {
    historyDays = Number(b.dataset.days);
    try { localStorage.setItem("days", historyDays); } catch {}
    loadHistory();
  };
});
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (state.history) { socChart(state.history.points, historyDays); kmChart(state.history.daily_km, historyDays); }
  }, 150);
});

// ------------------------------------------------------------------------
// Benachrichtigungen
// ------------------------------------------------------------------------

function keyToBytes(b64) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

const notify = { reg: null, sub: null, info: null };

function renderNotify() {
  const on = !!(notify.sub && notify.info?.subscribed);
  $("notifyCard").classList.toggle("on", on);
  $("notifyState").textContent = on ? "Aktiv" : "Aus";
  $("notifyBtnText").textContent = on ? "Auf diesem Gerät deaktivieren" : "Auf diesem Gerät aktivieren";
  $("notifyBtn").classList.toggle("on", false);
  $("notifyTest").hidden = !on;
  document.querySelectorAll("#notifyCard input[data-level]").forEach((i) => {
    i.checked = !!notify.info?.settings[i.dataset.level];
  });
}

async function initNotify() {
  const btn = $("notifyBtn");
  const unsupported = (msg) => { $("notifyHint").textContent = msg; btn.disabled = true; btn.classList.add("busy"); };
  try {
    notify.info = await api("/api/notify");
  } catch { return; }
  renderNotify();
  if (!window.isSecureContext) {
    return unsupported("Benachrichtigungen gehen nur über eine https://-Adresse, z.B. die Tailscale-Adresse.");
  }
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    const ios = /iPhone|iPad/.test(navigator.userAgent);
    return unsupported(ios
      ? "Am iPhone: App zuerst über Teilen → „Zum Home-Bildschirm“ hinzufügen und von dort öffnen (ab iOS 16.4)."
      : "Dieser Browser unterstützt keine Push-Benachrichtigungen.");
  }
  notify.reg = await navigator.serviceWorker.ready;
  notify.sub = await notify.reg.pushManager.getSubscription();
  if (notify.sub) {
    notify.info = await api(`/api/notify?endpoint=${encodeURIComponent(notify.sub.endpoint)}`);
  }
  renderNotify();
}

async function subscribePush() {
  const perm = await Notification.requestPermission();
  if (perm !== "granted") throw new Error("Benachrichtigungen wurden im Browser nicht erlaubt.");
  const opts = { userVisibleOnly: true, applicationServerKey: keyToBytes(notify.info.public_key) };
  try {
    notify.sub = await notify.reg.pushManager.subscribe(opts);
  } catch (e) {
    // altes Abo mit anderem Schlüssel: abmelden und neu versuchen
    const old = await notify.reg.pushManager.getSubscription();
    if (!old) throw e;
    await old.unsubscribe();
    notify.sub = await notify.reg.pushManager.subscribe(opts);
  }
  await api("/api/notify/subscribe", { method: "POST", body: JSON.stringify(notify.sub.toJSON()) });
}

$("notifyBtn").onclick = async () => {
  const btn = $("notifyBtn");
  btn.classList.add("busy");
  try {
    if (notify.sub && notify.info?.subscribed) {
      await api("/api/notify/unsubscribe", { method: "POST", body: JSON.stringify(notify.sub.toJSON()) });
      await notify.sub.unsubscribe();
      notify.sub = null;
      toast("Benachrichtigungen auf diesem Gerät deaktiviert");
    } else {
      await subscribePush();
      toast("✅ Benachrichtigungen aktiviert");
    }
    notify.info = await api(`/api/notify${notify.sub ? "?endpoint=" + encodeURIComponent(notify.sub.endpoint) : ""}`);
  } catch (e) {
    toast(`⚠️ ${e.message}`);
  } finally {
    btn.classList.remove("busy");
    renderNotify();
  }
};

$("notifyTest").onclick = async () => {
  try {
    const r = await api("/api/notify/test", { method: "POST" });
    toast(r.errors?.length ? `Gesendet, aber: ${r.errors.join("; ")}` : "Test-Benachrichtigung gesendet – kommt in wenigen Sekunden");
  } catch (e) {
    toast(`⚠️ ${e.message}`);
  }
};

document.querySelectorAll("#notifyCard input[data-level]").forEach((input) => {
  input.onchange = async () => {
    try {
      const settings = await api("/api/notify/settings", {
        method: "POST", body: JSON.stringify({ [input.dataset.level]: input.checked }),
      });
      notify.info.settings = settings;
    } catch (e) {
      toast(`⚠️ ${e.message}`);
    }
    renderNotify();
  };
});

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});

load().then(() => { loadHistory(); initNotify(); });
setInterval(() => { if (!document.hidden) loadHistory(); }, 5 * 60000);
