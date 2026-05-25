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
    if (btn.dataset.tab === "calendar" && __state.loggedIn) { loadCalendar(); loadBackups("calendar"); }
    if (btn.dataset.tab === "todos" && __state.loggedIn) { loadTodos(); loadBackups("tasks"); }
    if (btn.dataset.tab === "safemode" && __state.loggedIn) loadSafeMode();
    if (btn.dataset.tab === "chat" && __state.loggedIn) { loadChatMessages(); loadChatContextStatus(); }
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
let __todoLists = []; // [ { id, title } ]

async function loadTodos() {
  if (!__state.loggedIn) return;
  const status = el("todos-status");
  const subtabs = el("todos-subtabs");
  const content = el("todos-content");

  // First, fetch task lists
  try {
    const listsData = await api("GET", "/api/tasks/lists");
    __todoLists = (listsData.items || []).map(l => ({ id: l.id, title: l.title || l.id }));
  } catch(e) {
    status.textContent = "Fehler beim Laden der Listen";
    status.classList.remove("hidden");
    return;
  }

  // Build subtabs
  subtabs.innerHTML = "";
  const activeListId = subtabs.dataset.active || (__todoLists[0] ? __todoLists[0].id : "");

  for (const tl of __todoLists) {
    const btn = document.createElement("button");
    btn.className = "subtab" + (tl.id === activeListId ? " active" : "");
    btn.textContent = tl.title;
    btn.addEventListener("click", () => {
      subtabs.dataset.active = tl.id;
      loadTodos();
    });
    subtabs.appendChild(btn);
  }

  if (__todoLists.length > 1) {
    const allBtn = document.createElement("button");
    allBtn.className = "subtab" + (activeListId === "__all__" ? " active" : "");
    allBtn.textContent = "Alle";
    allBtn.addEventListener("click", () => {
      subtabs.dataset.active = "__all__";
      loadTodos();
    });
    subtabs.appendChild(allBtn);
  }

  // Fetch tasks for selected list
  content.innerHTML = "Lade Todos…";
  try {
    const showCompleted = el("todos-show-completed").checked;
    let tlParam = activeListId && activeListId !== "__all__" ? ("?tasklist=" + encodeURIComponent(activeListId)) : "";
    tlParam += (tlParam ? "&" : "?") + "show_completed=" + (showCompleted ? "1" : "0");
    const data = await api("GET", "/api/tasks" + tlParam);
    if (data.error) {
      status.textContent = errorText(data.error);
      status.classList.remove("hidden");
      content.innerHTML = "";
      return;
    }
    status.classList.add("hidden");
    const items = Array.isArray(data.items) ? data.items : [];
    if (!items.length) { content.innerHTML = "<p style='color:var(--muted)'>Keine offenen Todos.</p>"; return; }

    // If "Alle", group by list
    if (activeListId === "__all__") {
      const groups = {};
      for (const t of items) {
        const tlTitle = t._tasklist_title || "Standard";
        groups[tlTitle] = groups[tlTitle] || [];
        groups[tlTitle].push(t);
      }
      content.innerHTML = Object.entries(groups).map(([title, tasks]) => {
        const taskItems = tasks.map(t => {
          const done = t.status === "completed";
          return `<li class="todo-card ${done ? "done" : ""}"><span class="todo-title">${done ? "☑" : "☐"} ${h(t.title || "Ohne Titel")}</span>${t.notes ? '<div class="todo-meta">' + h(t.notes) + '</div>' : ''}</li>`;
        }).join("");
        return `<div class="list-heading">📋 ${h(title)}</div><ul class="todos-list">${taskItems}</ul>`;
      }).join("");
    } else {
      content.innerHTML = `<ul class="todos-list">${items.map(t => {
        const done = t.status === "completed";
        return `<li class="todo-card ${done ? "done" : ""}"><span class="todo-title">${done ? "☑" : "☐"} ${h(t.title || "Ohne Titel")}</span>${t.notes ? '<div class="todo-meta">' + h(t.notes) + '</div>' : ''}</li>`;
      }).join("")}</ul>`;
    }
  } catch(e) {
    content.textContent = "Fehler: " + e.message;
  }
}

// ---- Safe Mode ----
async function loadBackups(area) {
  if (!__state.loggedIn) return;
  const box = el(area === "tasks" ? "tasks-backups" : "calendar-backups");
  if (!box || box.classList.contains("hidden")) return;
  box.textContent = "Lade Backups…";
  try {
    const data = await api("GET", "/api/backups?area=" + encodeURIComponent(area));
    const backups = data.backups || [];
    if (!backups.length) {
      box.innerHTML = "<p class='muted'>Noch keine Backups. Vor der nächsten Änderung wird automatisch eines angelegt.</p>";
      return;
    }
    box.innerHTML = backups.map(b => {
      const counts = b.counts || {};
      const countText = area === "tasks"
        ? `${counts.tasklists || 0} Listen, ${counts.tasks || 0} Tasks`
        : `${counts.calendars || 0} Kalender, ${counts.events || 0} Termine`;
      return `<div class="backup-item">
        <div><strong>${fmtDt(b.created_at || "")}</strong><div class="backup-meta">${h(countText)} · ${h(b.reason || b.action_type || "Backup")}</div></div>
        <button class="btn-danger btn-sm apply-backup" data-area="${h(area)}" data-id="${h(b.id)}" type="button">Wiederherstellen</button>
      </div>`;
    }).join("");
    box.querySelectorAll(".apply-backup").forEach(btn => {
      btn.addEventListener("click", () => applyBackup(btn.dataset.area, btn.dataset.id));
    });
  } catch(e) {
    box.textContent = "Fehler: " + e.message;
  }
}

async function toggleBackups(area) {
  const box = el(area === "tasks" ? "tasks-backups" : "calendar-backups");
  if (!box) return;
  box.classList.toggle("hidden");
  if (!box.classList.contains("hidden")) await loadBackups(area);
}

async function applyBackup(area, id) {
  const label = area === "tasks" ? "Tasks" : "Kalender";
  if (!confirm(`${label}-Backup wirklich wiederherstellen?\n\nDas verändert reale Google-Daten. Direkt davor wird automatisch ein Sicherheitsbackup angelegt.`)) return;
  try {
    const res = await api("POST", "/api/backups/apply", { area, id });
    if (!res.ok) {
      alert("Restore-Freigabe fehlgeschlagen: " + errorText(res.error || res.result || "Unbekannt"));
      return;
    }
    if (res.pending) {
      alert("Restore wurde als Safe-Mode-Freigabe angelegt. Bitte im Tab Freigaben genehmigen.");
      await refreshStatus();
      return;
    }
    alert("Backup wiederhergestellt. Sicherheitsbackup wurde angelegt.");
    if (area === "tasks") { await loadTodos(); await loadBackups("tasks"); }
    if (area === "calendar") { await loadCalendar(); await loadBackups("calendar"); }
  } catch(e) {
    alert("Fehler: " + e.message);
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
    container.innerHTML = `<div class="bulk-actions"><button id="btn-approve-all" class="btn-ok" type="button">✅ Alle ${actions.length} freigeben</button><span class="action-meta">Legt pro betroffenem Bereich nur ein Backup an.</span></div>` + actions.map(a => {
      const p = a.payload || {};
      const detailLines = Object.entries(p)
        .filter(([k]) => !["calendar_id", "tasklist_id"].includes(k))
        .map(([k,v]) => `<span class="action-meta">${h(k)}: ${h(String(JSON.stringify(v)).slice(0,120))}</span>`)
        .join("<br>");
      return `<div class="action-card" id="act-${h(a.id)}">
        <div class="action-title">🛡️ ${h(a.title)}</div>
        <div class="action-meta">Typ: ${h(a.type)} | Quelle: ${h(a.source)} | Status: ${h(a.status)}</div>
        ${detailLines ? '<div class="action-detail">' + detailLines + '</div>' : ''}
        <div class="action-actions">
          <button class="btn-ok approve-btn" data-id="${h(a.id)}">✅ Genehmigen</button>
          <button class="btn-secondary edit-btn" data-id="${h(a.id)}">✏️ Bearbeiten</button>
          <button class="btn-danger reject-btn" data-id="${h(a.id)}">❌ Ablehnen</button>
          <button class="btn-danger delete-btn" data-id="${h(a.id)}">🗑️ Löschen</button>
        </div>
        <div class="edit-form hidden" id="edit-form-${h(a.id)}">
          <div class="setting-row"><label>Titel</label><input type="text" id="edit-title-${h(a.id)}" value="${h(a.title)}"></div>
          <div class="setting-row"><label>Payload (JSON)</label><textarea id="edit-payload-${h(a.id)}" rows="4">${h(JSON.stringify(a.payload || {}, null, 2))}</textarea></div>
          <div class="setting-actions">
            <button class="btn-primary save-edit-btn" data-id="${h(a.id)}">💾 Speichern</button>
            <button class="btn-secondary cancel-edit-btn" data-id="${h(a.id)}">Abbrechen</button>
          </div>
        </div>
      </div>`;
    }).join("");
    const approveAllBtn = el("btn-approve-all");
    if (approveAllBtn) approveAllBtn.addEventListener("click", () => approveAll(actions.length));
    container.querySelectorAll(".approve-btn").forEach(b => {
      b.addEventListener("click", () => approve(b.dataset.id));
    });
    container.querySelectorAll(".reject-btn").forEach(b => {
      b.addEventListener("click", () => reject(b.dataset.id));
    });
    container.querySelectorAll(".edit-btn").forEach(b => {
      b.addEventListener("click", () => toggleEditForm(b.dataset.id));
    });
    container.querySelectorAll(".cancel-edit-btn").forEach(b => {
      b.addEventListener("click", () => toggleEditForm(b.dataset.id));
    });
    container.querySelectorAll(".save-edit-btn").forEach(b => {
      b.addEventListener("click", () => saveEdit(b.dataset.id));
    });
    container.querySelectorAll(".delete-btn").forEach(b => {
      b.addEventListener("click", () => deleteAction(b.dataset.id));
    });
  } catch(e) {
    container.textContent = "Fehler: " + e.message;
  }
}

async function refreshAfterApproval() {
  await refreshStatus();
  await loadSafeMode();
  if (document.getElementById("tab-todos").classList.contains("active")) { await loadTodos(); await loadBackups("tasks"); }
  if (document.getElementById("tab-calendar").classList.contains("active")) { await loadCalendar(); await loadBackups("calendar"); }
}

async function approveAll(count) {
  if (!confirm(`${count} Freigaben wirklich alle ausführen?\n\nEs wird pro betroffenem Bereich nur ein Backup vor der Batch-Ausführung angelegt.`)) return;
  const bulk = el("safemode-list");
  if (bulk) {
    const bar = bulk.querySelector(".bulk-actions");
    if (bar) bar.innerHTML = `<span class="approval-status status-busy">⏳ Führe ${count} Aktionen aus…</span>`;
  }
  try {
    const res = await api("POST", "/api/safe-mode/approve-all", {});
    if (res.ok) {
      await refreshAfterApproval();
      alert(`${res.approved || 0} Aktionen freigegeben.`);
    } else {
      await refreshAfterApproval();
      alert(`Batch-Freigabe teilweise/komplett fehlgeschlagen: ${res.approved || 0} OK, ${res.failed || 0} Fehler. ${res.error || ""}`);
    }
  } catch(e) { alert("Fehler: " + e.message); await loadSafeMode(); }
}

async function approve(id) {
  const card = el("act-" + id);
  if (!card) return;
  card.classList.add("busy");
  showApprovalBusy(card, "✅ Wird freigegeben…");
  try {
    const res = await api("POST", "/api/safe-mode/approve", { id });
    if (res.ok) {
      showApprovalResult(card, "✅ Freigegeben", "ok");
      await refreshAfterApproval();
    } else {
      showApprovalResult(card, "❌ " + (res.error || "Fehler"), "err");
      setTimeout(() => loadSafeMode(), 1800);
    }
  } catch(e) {
    showApprovalResult(card, "❌ " + e.message, "err");
    setTimeout(() => loadSafeMode(), 1800);
  }
}

async function reject(id) {
  const card = el("act-" + id);
  if (!card) return;
  card.classList.add("busy");
  showApprovalBusy(card, "❌ Wird abgelehnt…");
  try {
    const res = await api("POST", "/api/safe-mode/reject", { id });
    if (res.ok) {
      showApprovalResult(card, "❌ Abgelehnt", "rej");
      await refreshStatus();
      setTimeout(() => loadSafeMode(), 800);
    } else {
      showApprovalResult(card, "❌ " + (res.error || "Fehler"), "err");
      setTimeout(() => loadSafeMode(), 1800);
    }
  } catch(e) {
    showApprovalResult(card, "❌ " + e.message, "err");
    setTimeout(() => loadSafeMode(), 1800);
  }
}

function toggleEditForm(id) {
  const form = el("edit-form-" + id);
  if (!form) return;
  form.classList.toggle("hidden");
}

async function saveEdit(id) {
  const titleInp = el("edit-title-" + id);
  const payloadTa = el("edit-payload-" + id);
  if (!titleInp || !payloadTa) return;
  let payload;
  try {
    payload = JSON.parse(payloadTa.value);
  } catch(e) {
    alert("Ungueltiges JSON im Payload: " + e.message);
    return;
  }
  const card = el("act-" + id);
  if (card) card.classList.add("busy");
  try {
    const res = await api("POST", "/api/safe-mode/edit", { id, title: titleInp.value.trim(), payload });
    if (res.ok) {
      await loadSafeMode();
    } else {
      alert("Bearbeiten fehlgeschlagen: " + (res.error || "Unbekannter Fehler"));
      if (card) card.classList.remove("busy");
    }
  } catch(e) {
    alert("Bearbeiten fehlgeschlagen: " + e.message);
    if (card) card.classList.remove("busy");
  }
}

async function deleteAction(id) {
  if (!confirm("Aktion wirklich loeschen? Dies kann nicht rueckgaengig gemacht werden.")) return;
  const card = el("act-" + id);
  if (card) card.classList.add("busy");
  try {
    const res = await api("POST", "/api/safe-mode/delete", { id });
    if (res.ok) {
      await loadSafeMode();
      await refreshStatus();
    } else {
      alert("Loeschen fehlgeschlagen: " + (res.error || "Unbekannter Fehler"));
      if (card) card.classList.remove("busy");
    }
  } catch(e) {
    alert("Loeschen fehlgeschlagen: " + e.message);
    if (card) card.classList.remove("busy");
  }
}

function showApprovalBusy(card, msg) {
  const actions = card.querySelector(".action-actions");
  if (actions) actions.innerHTML = `<span class="approval-status status-busy">${h(msg)}</span>`;
}

function showApprovalResult(card, msg, cls) {
  const actions = card.querySelector(".action-actions");
  if (actions) actions.innerHTML = `<span class="approval-status status-${cls}">${h(msg)}</span>`;
}

// ---- Chat ----
async function loadChatMessages() {
  if (!__state.loggedIn) return;
  const msgs = el("chat-messages");
  try {
    const data = await api("GET", "/api/chat/messages?channel=web");
    const messages = data.messages || [];
    renderChatMessages(messages);
    loadChatContextStatus();
    _checkPendingAfterLoad(messages);
  } catch(e) {
    msgs.textContent = "Fehler: " + e.message;
  }
}

let _pollTimer = 0;

function _checkPendingAfterLoad(messages) {
  if (messages.length && messages[messages.length - 1].role === "user") {
    showThinkingIndicator();
    if (!_pollTimer) _pollTimer = setInterval(_pollChatPending, 1500);
  } else {
    hideThinkingIndicator();
    clearPollTimer();
  }
}

function clearPollTimer() {
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = 0; }
}

async function _pollChatPending() {
  try {
    const s = await api("GET", "/api/chat/pending");
    if (!s.pending) {
      clearPollTimer();
      await loadChatMessages();
      await loadChatContextStatus();
    }
  } catch(e) {}
}

function renderChatMessages(messages) {
  const msgs = el("chat-messages");
  if (!messages.length) {
    msgs.innerHTML = '<div class="msg assistant">Hallo! Ich bin dein Assistent. Frag mich, was heute ansteht, oder sag mir, was ich in Kalender/Todos eintragen soll.</div>';
    return;
  }
  msgs.innerHTML = messages.map(m => chatMessageHtml(m.role, m.content, m.attachments || [])).join("");
  msgs.scrollTop = msgs.scrollHeight;
}

function chatMessageHtml(role, content, attachments) {
  let attachmentHtml = "";
  if (attachments && attachments.length) {
    const items = attachments.map(a => {
      const mime = a.mime_type || "";
      const isImage = mime.startsWith("image/");
      const isAudio = mime.startsWith("audio/");
      const isPdf = mime === "application/pdf";
      const url = a.url || `/assistant/api/upload/${a.id}`;
      if (isImage) return `<div class="atch-thumb"><img src="${url}" alt="${h(a.filename)}"></div>`;
      if (isAudio) return `<div class="atch-audio"><span>🎧 ${h(a.filename)} (${fmtBytes(a.size || 0)})</span><audio controls src="${url}"></audio></div>`;
      if (isPdf) return `<a class="atch-link" href="${url}" target="_blank" rel="noopener">📄 ${h(a.filename)} (${fmtBytes(a.size || 0)})</a>`;
      return `<a class="atch-link" href="${url}" target="_blank" rel="noopener">📎 ${h(a.filename)} (${fmtBytes(a.size || 0)})</a>`;
    }).join("");
    attachmentHtml = `<div class="msg-attachments">${items}</div>`;
  }
  return `<div class="msg ${role}"><div class="role">${role === "user" ? "Du" : "🤖 Assistant"}</div>${md(content)}${attachmentHtml}</div>`;
}

function fmtBytes(b) {
  if (b < 1024) return b + " B";
  if (b < 1024*1024) return Math.round(b/1024) + " KB";
  return Math.round(b/(1024*1024)) + " MB";
}

function showThinkingIndicator() {
  const msgs = el("chat-messages");
  const existing = el("chat-thinking");
  if (existing) existing.remove();
  msgs.insertAdjacentHTML("beforeend", `
    <div id="chat-thinking" class="msg assistant thinking" aria-live="polite">
      <div class="role">🤖 Assistant</div>
      <span class="thinking-label">denkt</span>
      <span class="thinking-dots" aria-hidden="true"><i></i><i></i><i></i></span>
    </div>
  `);
  msgs.scrollTop = msgs.scrollHeight;
}

function hideThinkingIndicator() {
  const thinking = el("chat-thinking");
  if (thinking) thinking.remove();
}

el("chat-input").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && ev.ctrlKey) {
    ev.preventDefault();
    el("chat-form").requestSubmit();
  }
});

el("btn-compact-context").addEventListener("click", async () => {
  if (!__state.loggedIn) { alert("Bitte zuerst mit Google anmelden."); return; }
  const btn = el("btn-compact-context");
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = "Kompaktiere…";
  try {
    const res = await api("POST", "/api/chat/context/compact", {});
    if (res.error) alert("Fehler: " + errorText(res.error));
    if (res.messages) renderChatMessages(res.messages);
    else await loadChatMessages();
    await loadChatContextStatus();
  } catch(e) {
    alert("Fehler: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
});

el("btn-clear-chat").addEventListener("click", async () => {
  if (!__state.loggedIn) { alert("Bitte zuerst mit Google anmelden."); return; }
  if (!confirm("Gesamten Chat-Verlauf & Kontext unwiderruflich löschen?")) return;
  const btn = el("btn-clear-chat");
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = "Lösche…";
  try {
    const res = await api("POST", "/api/chat/clear", {});
    if (res.error) alert("Fehler: " + errorText(res.error));
    if (res.messages) renderChatMessages(res.messages);
    else await loadChatMessages();
    await loadChatContextStatus();
  } catch(e) {
    alert("Fehler: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
});

async function loadChatContextStatus() {
  const box = el("chat-context-info");
  if (!box || !__state.loggedIn) return;
  try {
    const s = await api("GET", "/api/chat/context/status");
    if (s.error) { box.textContent = "Kontext: " + errorText(s.error); return; }
    const pct = Number(s.used_percent || 0);
    box.innerHTML = `Provider/LLM: <strong>${h(s.provider || "?")} / ${h(s.model || "?")}</strong> · Kontext: <strong>${s.used_tokens || 0}</strong>/<strong>${s.max_tokens || 0}</strong> Tokens (${pct}%) · Auto-Kompakt ab ${s.auto_compact_at_percent || 80}%${s.summary_chars ? " · kompakt: " + s.summary_chars + " Zeichen" : ""}`;
    box.classList.toggle("warn", pct >= 70);
  } catch(e) {
    box.textContent = "Kontextstatus nicht verfügbar";
  }
}

let __pendingAttachments = [];

function renderAttachmentPreview() {
  const box = el("chat-attachments");
  if (!box) return;
  if (!__pendingAttachments.length) { box.innerHTML = ""; return; }
  box.innerHTML = __pendingAttachments.map(a => {
    const mime = a.mime_type || "";
    const isImage = mime.startsWith("image/");
    const isAudio = mime.startsWith("audio/");
    const isPdf = mime === "application/pdf";
    let thumb;
    if (isImage) thumb = `<img src="${a.previewUrl || a.url}" class="atch-preview-img">`;
    else if (isAudio) thumb = `<span class="atch-preview-icon atch-preview-audio">🎧</span>`;
    else if (isPdf) thumb = `<span class="atch-preview-icon atch-preview-pdf">📄</span>`;
    else thumb = `<span class="atch-preview-icon">📎</span>`;
    return `<div class="atch-preview" data-id="${h(a.id)}">${thumb}<span class="atch-preview-name">${h(a.filename)}</span><button class="atch-remove" type="button" title="Entfernen">×</button></div>`;
  }).join("");
  box.querySelectorAll(".atch-remove").forEach(btn => {
    btn.addEventListener("click", () => {
      const id = btn.closest(".atch-preview").dataset.id;
      __pendingAttachments = __pendingAttachments.filter(a => a.id !== id);
      renderAttachmentPreview();
    });
  });
}

async function uploadFile(file) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(API + "/api/upload", { method: "POST", body: form, credentials: "include" });
  const text = await res.text();
  try {
    const data = text ? JSON.parse(text) : {};
    if (data.error) throw new Error(data.error);
    return data;
  } catch (e) {
    throw new Error("Upload fehlgeschlagen: " + text.slice(0, 200));
  }
}

el("btn-attach").addEventListener("click", () => {
  el("chat-file-input").click();
});

el("chat-file-input").addEventListener("change", async (ev) => {
  const files = Array.from(ev.target.files || []);
  if (!files.length) return;
  for (const file of files) {
    try {
      const previewUrl = (file.type || "").startsWith("image/") ? URL.createObjectURL(file) : null;
      const up = await uploadFile(file);
      __pendingAttachments.push({ id: up.id, filename: up.filename, mime_type: up.mime_type, size: up.size, url: up.url, previewUrl });
    } catch (e) {
      alert("Upload-Fehler: " + e.message);
    }
  }
  renderAttachmentPreview();
  ev.target.value = "";
});

el("chat-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (!__state.loggedIn) { alert("Bitte zuerst mit Google anmelden."); return; }
  const input = el("chat-input");
  const text = input.value.trim();
  if (!text && !__pendingAttachments.length) return;
  const attachmentsToSend = [...__pendingAttachments];
  __pendingAttachments = [];
  renderAttachmentPreview();
  input.value = "";
  input.disabled = true;
  const submitBtn = el("chat-form").querySelector("button[type=submit]");
  if (submitBtn) submitBtn.disabled = true;
  const msgs = el("chat-messages");
  if (msgs) {
    if (msgs.querySelector(".msg") && !msgs.querySelector(".msg .role")) msgs.innerHTML = "";
    msgs.insertAdjacentHTML("beforeend", chatMessageHtml("user", text, attachmentsToSend));
    showThinkingIndicator();
  }
  try {
    const payload = { text, attachments: attachmentsToSend.map(a => a.id) };
    const res = await api("POST", "/api/ai/chat", payload);
    if (res.status === "processing") {
      if (!_pollTimer) _pollTimer = setInterval(_pollChatPending, 1500);
    } else {
      clearPollTimer();
      await loadChatMessages();
      await loadChatContextStatus();
    }
  } catch(e) {
    hideThinkingIndicator();
    alert("Fehler: " + e.message);
    clearPollTimer();
  } finally {
    input.disabled = false;
    if (submitBtn) submitBtn.disabled = false;
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
    ai: ["AI_BASE_URL", "AI_API_KEY", "AI_MODEL", "AI_THINK_EFFORT", "AI_CONTEXT_MAX_TOKENS", "timezone", "location"],
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

// ---- Export Dialog ----
function doExport(format) {
  if (!__state.loggedIn) return alert("Bitte zuerst mit Google anmelden.");
  let activeListId = el("todos-subtabs").dataset.active || "";
  // Fallback to first list if no subtab clicked yet
  if (!activeListId && __todoLists.length > 0) activeListId = __todoLists[0].id;
  // If "Alle" view active, skip dialog and export all
  if (activeListId === "__all__") {
    _exportUrl(format, true, activeListId);
    return;
  }
  // If only one list, skip dialog
  if (__todoLists.length <= 1) {
    _exportUrl(format, true, activeListId);
    return;
  }
  // Show custom dialog
  el("export-dialog").classList.remove("hidden");
  const allBtn = el("export-dialog-all");
  const curBtn = el("export-dialog-current");
  const closeBtn = el("export-dialog-close");
  const dialog = el("export-dialog");
  const handler = (all) => {
    dialog.classList.add("hidden");
    allBtn.removeEventListener("click", onAll);
    curBtn.removeEventListener("click", onCur);
    closeBtn.removeEventListener("click", onClose);
    dialog.removeEventListener("click", onBg);
    _exportUrl(format, all, activeListId);
  };
  const onAll = () => handler(true);
  const onCur = () => handler(false);
  const onClose = () => {
    dialog.classList.add("hidden");
    allBtn.removeEventListener("click", onAll);
    curBtn.removeEventListener("click", onCur);
    closeBtn.removeEventListener("click", onClose);
    dialog.removeEventListener("click", onBg);
  };
  const onBg = (e) => { if (e.target === dialog) onClose(); };
  allBtn.addEventListener("click", onAll);
  curBtn.addEventListener("click", onCur);
  closeBtn.addEventListener("click", onClose);
  dialog.addEventListener("click", onBg);
}

function _exportUrl(format, all, activeListId) {
  const showCompleted = el("todos-show-completed").checked ? "1" : "0";
  const ext = format === "pdf" ? "/pdf" : "";
  let url = API + "/api/tasks/export" + ext + "?show_completed=" + showCompleted;
  if (!all && activeListId && activeListId !== "__all__") url += "&tasklist=" + encodeURIComponent(activeListId);
  window.open(url, "_blank");
}

// ---- Init ----
(async function init() {
  await checkAuth();
  if (__state.loggedIn) {
    loadCalendar();
  }
  el("todos-show-completed").addEventListener("change", () => {
    if (document.getElementById("tab-todos").classList.contains("active")) loadTodos();
  });

  el("btn-calendar-backups").addEventListener("click", () => toggleBackups("calendar"));
  el("btn-tasks-backups").addEventListener("click", () => toggleBackups("tasks"));

  // Export/Import buttons
  el("btn-export-json").addEventListener("click", () => doExport("json"));
  el("btn-export-pdf").addEventListener("click", () => doExport("pdf"));

  el("export-dialog-close").addEventListener("click", () => el("export-dialog").classList.add("hidden"));
  el("export-dialog").addEventListener("click", (e) => {
    if (e.target === el("export-dialog")) el("export-dialog").classList.add("hidden");
  });

  el("btn-import-json").addEventListener("click", () => {
    if (!__state.loggedIn) return alert("Bitte zuerst mit Google anmelden.");
    el("import-file-input").click();
  });

  el("import-file-input").addEventListener("change", async (ev) => {
    const file = ev.target.files[0];
    if (!file) return;
    try {
      const text = await file.text();
      const res = await api("POST", "/api/tasks/import", JSON.parse(text));
      if (res.pending) {
        alert("Import wurde als Safe-Mode-Freigabe angelegt. Bitte im Tab Freigaben genehmigen.");
        await refreshStatus();
      } else if (res.created !== undefined) {
        alert(res.created + " Tasks importiert" + (res.errors ? ", " + res.errors + " Fehler" : ""));
        if (document.getElementById("tab-todos").classList.contains("active")) loadTodos();
      } else {
        alert("Fehler: " + (res.error || "Unbekannt"));
      }
    } catch(e) {
      alert("Fehler beim Import: " + e.message);
    }
    ev.target.value = "";
  });

  setInterval(refreshStatus, 30000);
})();
