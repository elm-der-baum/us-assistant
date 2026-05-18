// assistant app.js – Google Account = identity

const BASE = window.location.pathname.startsWith("/assistant") ? "/assistant" : "";
const API = BASE;
let __state = { loggedIn: false, email: "", appConfigured: false, pendingCount: 0 };

// ---- Tab switching ----
document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(b => { b.classList.remove("active"); b.setAttribute("aria-selected", "false"); });
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    btn.setAttribute("aria-selected", "true");
    document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "calendar" && __state.loggedIn) loadCalendar();
    if (btn.dataset.tab === "todos" && __state.loggedIn) loadTodos();
    if (btn.dataset.tab === "safemode" && __state.loggedIn) loadSafeMode();
    if (btn.dataset.tab === "chat" && __state.loggedIn) loadChatMessages();
    if (btn.dataset.tab === "settings") loadSettings();
  });
});

// ---- API helpers ----
async function api(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" }, credentials: "include" };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(API + path, opts);
  const text = await res.text();
  try {
    return text ? JSON.parse(text) : {};
  } catch (e) {
    return { error: `HTTP ${res.status}: ${text.slice(0, 200)}` };
  }
}

function el(id) { return document.getElementById(id); }

function errorText(err) {
  if (!err) return "Unbekannter Fehler";
  if (err === "no_valid_token") return "Nicht mit Google verbunden. Bitte unter Einstellungen mit Google anmelden.";
  if (typeof err === "string") return err;
  if (typeof err === "object") {
    if (err.message) return err.message;
    if (err.error_description) return err.error_description;
    if (err.status && err.message) return `${err.status}: ${err.message}`;
    try { return JSON.stringify(err, null, 2); } catch(e) { return String(err); }
  }
  return String(err);
}

// ---- Auth & State ----
async function checkAuth() {
  try {
    const s = await api("GET", "/api/auth/status");
    __state.loggedIn = !!s.logged_in;
    __state.email = s.email || "";
    __state.appConfigured = !!s.app_configured;
    updateUIForAuth();
    if (s.logged_in) {
      await refreshStatus();
    }
    return s;
  } catch(e) { return { logged_in: false, email: null }; }
}

function updateUIForAuth() {
  const gate = el("login-gate");
  const gateStatus = el("gate-status");
  const btnGateLogin = el("btn-gate-login");
  const btnGateSetup = el("btn-gate-setup");
  const btnHeaderLogin = el("btn-header-login");
  const btnLogout = el("btn-logout");
  const accountInfo = el("account-info");
  const googleAccountBox = el("google-account-box");
  const badge = el("badge-pending");

  if (__state.loggedIn) {
    gate.classList.add("hidden");
    accountInfo.textContent = "📧 " + __state.email;
    btnHeaderLogin.classList.add("hidden");
    btnLogout.classList.remove("hidden");
    badge.classList.toggle("hidden", !__state.pendingCount);
    if (googleAccountBox) googleAccountBox.innerHTML = "✅ Angemeldet als <strong>" + __state.email + "</strong>";
  } else {
    accountInfo.textContent = "Nicht angemeldet";
    btnHeaderLogin.classList.remove("hidden");
    btnLogout.classList.add("hidden");
    badge.classList.add("hidden");
    badge.textContent = "0";
    if (googleAccountBox) googleAccountBox.innerHTML = "⚠️ Nicht verbunden. Klicke „Google Account verbinden“ zum Einloggen.";
  }

  // Login gate logic
  if (__state.loggedIn) {
    gate.classList.add("hidden");
  } else {
    gate.classList.remove("hidden");
    if (__state.appConfigured) {
      gateStatus.innerHTML = "Google OAuth App ist konfiguriert.<br>Bitte mit deinem Google Account anmelden.";
      btnGateLogin.textContent = "Mit Google anmelden";
    } else {
      gateStatus.innerHTML = "Noch kein Google OAuth Client konfiguriert.<br>Vor dem Login bitte Client-ID und Client-Secret eintragen.";
      btnGateLogin.textContent = "Client-ID konfigurieren";
    }
  }
}

// ---- Status / Badge ----
async function refreshStatus() {
  try {
    const s = await api("GET", "/api/status");
    if (s) {
      __state.pendingCount = s.pending_count || 0;
      __state.loggedIn = !!s.logged_in;
      __state.email = s.email || __state.email;
      __state.appConfigured = !!s.app_configured;
      const badge = el("badge-pending");
      badge.textContent = s.pending_count || 0;
      badge.classList.toggle("hidden", !s.pending_count);
      updateUIForAuth();
    }
  } catch(e) {}
}

// ---- Calendar ----
async function loadCalendar() {
  if (!__state.loggedIn) return;
  const grid = el("calendar-grid");
  const status = el("calendar-status");
  grid.textContent = "Lade Kalender…";
  try {
    const data = await api("GET", "/api/calendar/events?timeMin=" + encodeURIComponent(new Date().toISOString()));
    if (data.error) {
      status.textContent = errorText(data.error);
      status.classList.remove("hidden");
      grid.innerHTML = "";
      return;
    }
    status.classList.add("hidden");
    const items = Array.isArray(data.items) ? data.items : [];
    if (!items.length) { grid.textContent = "Keine Termine gefunden."; return; }
    grid.innerHTML = items.slice(0, 30).map(ev => {
      const start = ev.start ? (ev.start.dateTime || ev.start.date) : "?";
      const end = ev.end ? (ev.end.dateTime || ev.end.date) : "?";
      return `<div class="event-card"><div class="event-title">${h(ev.summary || "Ohne Titel")}</div><div class="event-meta">${fmtDt(start)} → ${fmtDt(end)} ${ev.location ? "📍 " + h(ev.location) : ""}</div></div>`;
    }).join("");
  } catch(e) {
    grid.textContent = "Fehler: " + e.message;
  }
}

// ---- Todos ----
async function loadTodos() {
  if (!__state.loggedIn) return;
  const list = el("todos-list");
  const status = el("todos-status");
  list.innerHTML = "Lade Todos…";
  try {
    const data = await api("GET", "/api/tasks");
    if (data.error) {
      status.textContent = errorText(data.error);
      status.classList.remove("hidden");
      list.innerHTML = "";
      return;
    }
    status.classList.add("hidden");
    const items = Array.isArray(data.items) ? data.items : [];
    if (!items.length) { list.innerHTML = "Keine Todos gefunden."; return; }
    list.innerHTML = items.map(t => {
      const done = t.status === "completed";
      return `<li class="todo-card ${done ? "done" : ""}"><span class="todo-title">${done ? "☑" : "☐"} ${h(t.title || "Ohne Titel")}</span>${t.notes ? '<div class="todo-meta">' + h(t.notes) + '</div>' : ''}</li>`;
    }).join("");
  } catch(e) {
    list.textContent = "Fehler: " + e.message;
  }
}

// ---- Safe Mode ----
async function loadSafeMode() {
  if (!__state.loggedIn) return;
  const container = el("safemode-list");
  try {
    const data = await api("GET", "/api/safe-mode/pending");
    const actions = data.actions || [];
    await refreshStatus();
    if (!actions.length) { container.innerHTML = "<p>✅ Keine ausstehenden Freigaben.</p>"; return; }
    container.innerHTML = actions.map(a => {
      const p = a.payload || {};
      const detailLines = Object.entries(p).filter(([k]) => !["calendar_id", "tasklist_id"].includes(k)).map(([k,v]) => `<span class="action-meta">${k}: ${JSON.stringify(v).slice(0,120)}</span>`).join("<br>");
      return `<div class="action-card" id="act-${a.id}">
        <div class="action-title">🛡️ ${h(a.title)}</div>
        <div class="action-meta">Typ: ${a.type} | Quelle: ${a.source} | Status: ${a.status}</div>
        ${detailLines ? '<div class="action-detail">' + detailLines + '</div>' : ''}
        <div class="action-actions">
          <button class="btn-ok approve-btn" data-id="${a.id}">✅ Genehmigen</button>
          <button class="btn-danger reject-btn" data-id="${a.id}">❌ Ablehnen</button>
        </div>
      </div>`;
    }).join("");
    container.querySelectorAll(".approve-btn").forEach(b => {
      b.addEventListener("click", () => approve(b.dataset.id));
    });
    container.querySelectorAll(".reject-btn").forEach(b => {
      b.addEventListener("click", () => reject(b.dataset.id));
    });
  } catch(e) {
    container.textContent = "Fehler: " + e.message;
  }
}

async function approve(id) {
  try {
    const res = await api("POST", "/api/safe-mode/approve", { id });
    if (res.ok) {
      await refreshStatus();
      await loadSafeMode();
    } else {
      alert("Fehler: " + (res.error || "Unbekannt"));
    }
  } catch(e) { alert("Fehler: " + e.message); }
}

async function reject(id) {
  try {
    const res = await api("POST", "/api/safe-mode/reject", { id });
    if (res.ok) {
      await refreshStatus();
      await loadSafeMode();
    }
  } catch(e) { alert("Fehler: " + e.message); }
}

// ---- Chat ----
async function loadChatMessages() {
  if (!__state.loggedIn) return;
  const msgs = el("chat-messages");
  try {
    const data = await api("GET", "/api/chat/messages?channel=web");
    const messages = data.messages || [];
    if (!messages.length) { msgs.innerHTML = '<div class="msg assistant">Hallo! Ich bin dein Assistent. Frag mich, was heute ansteht, oder sag mir, was ich in Kalender/Todos eintragen soll.</div>'; return; }
    msgs.innerHTML = messages.map(m => `<div class="msg ${m.role}"><div class="role">${m.role === "user" ? "Du" : "🤖 Assistant"}</div>${md(m.content)}</div>`).join("");
    msgs.scrollTop = msgs.scrollHeight;
  } catch(e) {
    msgs.textContent = "Fehler: " + e.message;
  }
}

el("chat-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (!__state.loggedIn) { alert("Bitte zuerst mit Google anmelden."); return; }
  const input = el("chat-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  input.disabled = true;
  try {
    await api("POST", "/api/ai/chat", { text });
    await loadChatMessages();
    await refreshStatus();
  } catch(e) {
    alert("Fehler: " + e.message);
  } finally {
    input.disabled = false;
    input.focus();
  }
});

// ---- Settings ----
async function loadSettings() {
  try {
    const data = await api("GET", "/api/settings");
    const settings = data.settings || [];
    settings.forEach(s => {
      const inp = el("s-" + s.key);
      if (!inp) return;
      if (s.is_secret) {
        inp.value = s.masked || "";
        inp.dataset.hasValue = s.configured ? "1" : "0";
      } else {
        inp.value = s.value || "";
      }
    });
    checkGoogleAuthButton();
    await loadSystemStatus();
  } catch(e) {
    console.error("loadSettings", e);
  }
}

function getSettingValues(section) {
  const keys = {
    google: ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"],
    ai: ["AI_BASE_URL", "AI_API_KEY", "AI_MODEL"],
    telegram: ["TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_ID"],
  }[section];
  const secretKeys = new Set(["GOOGLE_CLIENT_SECRET", "AI_API_KEY", "TELEGRAM_BOT_TOKEN"]);
  const vals = {};
  keys.forEach(k => {
    const inp = el("s-" + k);
    if (!inp) return;
    const value = inp.value.trim();
    if (secretKeys.has(k) && inp.dataset.hasValue === "1" && (!value || value.includes("••••"))) return;
    vals[k] = inp.value;
  });
  return { vals };
}

function checkGoogleAuthButton() {
  const btn = el("btn-google-auth");
  const clientId = el("s-GOOGLE_CLIENT_ID");
  if (clientId && clientId.value.trim()) {
    btn.disabled = false;
    btn.textContent = "Google Account verbinden";
  } else {
    btn.disabled = true;
    btn.textContent = "Erst OAuth Setup (s. unten)";
  }
}

document.addEventListener("input", (ev) => {
  if (ev.target.id === "s-GOOGLE_CLIENT_ID") checkGoogleAuthButton();
  if (ev.target.type === "password") ev.target.dataset.hasValue = "0";
});

// ---- Setup Modal ----
function showSetupModal() {
  el("setup-modal").classList.remove("hidden");
  el("s-GOOGLE_CLIENT_ID").focus();
}

el("btn-open-google-setup").addEventListener("click", showSetupModal);
el("btn-gate-setup").addEventListener("click", showSetupModal);
el("btn-setup-close").addEventListener("click", () => el("setup-modal").classList.add("hidden"));

el("btn-save-google").addEventListener("click", async () => {
  const { vals } = getSettingValues("google");
  await api("POST", "/api/settings", vals);
  checkGoogleAuthButton();
  await checkAuth();
  await loadSystemStatus();
  el("google-setup-result").textContent = "✅ OAuth App gespeichert.";
  setTimeout(() => { el("google-setup-result").textContent = ""; }, 3000);
});

el("btn-save-google-login").addEventListener("click", async () => {
  const { vals } = getSettingValues("google");
  await api("POST", "/api/settings", vals);
  await checkAuth();
  doGoogleLogin();
});

// ---- Google Login ----
async function doGoogleLogin() {
  try {
    const res = await api("GET", "/api/google/auth-url");
    if (res.url) {
      window.location.href = res.url;
    } else {
      alert("Google OAuth App noch nicht konfiguriert. Bitte erst „OAuth App Setup\" ausführen.");
      showSetupModal();
    }
  } catch(e) { alert("Fehler: " + e.message); }
}

el("btn-google-auth").addEventListener("click", doGoogleLogin);
el("btn-gate-login").addEventListener("click", doGoogleLogin);
el("btn-header-login").addEventListener("click", doGoogleLogin);

// ---- Logout ----
el("btn-logout").addEventListener("click", async () => {
  await api("POST", "/api/auth/logout");
  __state.loggedIn = false;
  __state.email = "";
  updateUIForAuth();
  document.querySelector("[data-tab=calendar]").click();
  alert("Ausgeloggt.");
});

// ---- Save buttons ----
el("btn-save-ai").addEventListener("click", async () => {
  if (!__state.loggedIn) { alert("Bitte zuerst mit Google anmelden."); return; }
  const { vals } = getSettingValues("ai");
  await api("POST", "/api/settings", vals);
  alert("AI-Einstellungen gespeichert.");
});

el("btn-save-telegram").addEventListener("click", async () => {
  if (!__state.loggedIn) { alert("Bitte zuerst mit Google anmelden."); return; }
  const { vals } = getSettingValues("telegram");
  await api("POST", "/api/settings", vals);
  alert("Telegram-Einstellungen gespeichert.");
});

// ---- Tests ----
el("btn-test-google").addEventListener("click", async () => {
  const r = el("google-test-result");
  r.textContent = "Teste…";
  const res = await api("POST", "/api/google/test");
  r.textContent = res.ok ? "✅ Verbunden als " + res.email : "❌ " + (res.error || "Fehler");
});

el("btn-test-ai").addEventListener("click", async () => {
  const r = el("ai-test-result");
  r.textContent = "Teste…";
  const res = await api("POST", "/api/ai/test");
  r.textContent = res.ok ? "✅ OK – " + res.reply + " (" + res.ms + "ms)" : "❌ " + (res.error || "Fehler");
});

el("btn-test-telegram").addEventListener("click", async () => {
  const r = el("telegram-test-result");
  r.textContent = "Teste…";
  const res = await api("POST", "/api/telegram/test");
  r.textContent = res.ok ? "✅ Bot: " + res.bot : "❌ " + (res.error || "Fehler");
});

el("btn-refresh-status").addEventListener("click", loadSystemStatus);

async function loadSystemStatus() {
  const pre = el("system-status");
  try {
    const s = await api("GET", "/api/status");
    pre.textContent = JSON.stringify(s, null, 2);
  } catch(e) {
    pre.textContent = "Fehler: " + e.message;
  }
}

// ---- Password toggle ----
document.querySelectorAll(".toggle-pw").forEach(btn => {
  btn.addEventListener("click", () => {
    const inp = el(btn.dataset.for);
    if (!inp) return;
    if (inp.type === "password") {
      inp.type = "text";
      loadRealSecret(inp.id);
      btn.textContent = "🙈";
    } else {
      inp.type = "password";
      btn.textContent = "👁";
    }
  });
});

async function loadRealSecret(inputId) {
  try {
    const key = inputId.replace(/^s-/, "");
    const data = await api("POST", "/api/settings/secret", { key });
    if (data.value) {
      el(inputId).value = data.value;
    }
  } catch(e) {}
}

// ---- OAuth callback on load ----
(function() {
  const q = new URLSearchParams(window.location.search);
  if (q.get("google_ok") === "1") {
    alert("✅ Erfolgreich mit Google verbunden!");
    window.history.replaceState({}, "", BASE + "/");
  } else if (q.get("google_error") === "1") {
    alert("❌ Google-Verbindung fehlgeschlagen.");
    window.history.replaceState({}, "", BASE + "/");
  }
})();

// ---- Helpers ----
function h(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function md(s) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\n/g, "<br>");
}

function fmtDt(iso) {
  try {
    const d = new Date(iso);
    if (iso.length <= 10) return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
    return d.toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch(e) { return iso; }
}

// ---- Init ----
(async function init() {
  await checkAuth();
  if (__state.loggedIn) {
    loadCalendar();
  }
  setInterval(refreshStatus, 30000);
})();
