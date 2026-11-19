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
  toast.timer = setTimeout(() => t.classList.remove("show"), 3500);
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
  const on = !!state.data?.climate_on;
  $("climateCard").classList.toggle("on", on);
  $("climateState").textContent = on ? "Läuft" : "Aus";
  $("climateBtnText").textContent = on ? "Ausschalten" : "Einschalten";
  $("temp").textContent = fmt(state.temp, 1);
}

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

$("climateBtn").onclick = () => {
  const on = !state.data?.climate_on;
  runJob($("climateBtn"), () =>
    api("/api/climate", { method: "POST", body: JSON.stringify({ on, temperature: state.temp }) }),
    on ? "Klima ein" : "Klima aus");
};

document.querySelectorAll(".action").forEach((b) => {
  b.onclick = () => {
    if (b.dataset.cmd === "RDU" && !confirm("Auto wirklich entriegeln?")) return;
    runJob(b, () => api(`/api/command/${b.dataset.cmd}`, { method: "POST" }), b.textContent.trim());
  };
});

document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
setInterval(() => { if (!document.hidden) load(); }, 60000);

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});

load();
