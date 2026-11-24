// ChefScout web page. Plain JavaScript, no build step.
// Model text is always inserted with textContent (never innerHTML), and links come only from recipe cards.
"use strict";

const $ = (id) => document.getElementById(id);
const KIND_LABELS = { allergies: "אלרגיות", avoid: "לעולם לא", diet: "תזונה", kitchen_rules: "כללי מטבח", likes: "אוהבת" };
const KIND_OF_FIELD = { allergies: "allergy", avoid: "avoid", diet: "diet", kitchen_rules: "kitchen_rule", likes: "like" };
const RATING_LABELS = { loved: "😍", ok: "🙂", disliked: "👎" };
const GREETING = "היי! אני ChefScout 🍳\nספרי לי מה יש לך בבית, או בקשי רעיון לארוחה, ואמצא לך מתכונים אמיתיים מהרשת.";

let conversationId = loadConversation();
let busy = false;

// ---- small helpers -------------------------------------------------------------------
function loadConversation() {
  try { return localStorage.getItem("chefscout.conversation") || null; } catch { return null; }
}
function saveConversation(id) {
  conversationId = id;
  try { id ? localStorage.setItem("chefscout.conversation", id) : localStorage.removeItem("chefscout.conversation"); } catch { /* private mode */ }
}
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function safeUrl(url) {
  try { const u = new URL(url); return u.protocol === "https:" || u.protocol === "http:" ? u.href : null; } catch { return null; }
}
async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
function scrollDown() { window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" }); }

// ---- chat ------------------------------------------------------------------------------
function addMessage(role, text) {
  const node = el("div", `msg ${role}`, text);
  $("messages").append(node);
  scrollDown();
  return node;
}

function setBusy(on, label) {
  busy = on;
  $("send").disabled = on;
  $("progress").hidden = !on;
  $("progress-text").textContent = label || "חושב…";
}

async function send(text) {
  if (busy || !text.trim()) return;
  addMessage("user", text);
  setBusy(true, "חושב…");
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, conversation_id: conversationId }),
    });
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
    await readEvents(res.body);
  } catch (err) {
    addMessage("error", "לא הצלחתי להתחבר לשרת. האם הוא עדיין רץ?");
    console.error(err);
  } finally {
    setBusy(false);
    refreshSidebar();
  }
}

async function readEvents(body) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) !== -1) {
      handleEvent(buffer.slice(0, cut));
      buffer = buffer.slice(cut + 2);
    }
  }
}

function handleEvent(chunk) {
  let event = "message", data = "";
  for (const line of chunk.split("\n")) {
    if (line.startsWith("event: ")) event = line.slice(7);
    else if (line.startsWith("data: ")) data += line.slice(6);
  }
  const payload = data ? JSON.parse(data) : {};
  if (event === "start") saveConversation(payload.conversation_id);
  else if (event === "progress") $("progress-text").textContent = payload.label;
  else if (event === "answer") addMessage("agent", payload.text);
  else if (event === "recipes") renderRecipes(payload);
  else if (event === "error") addMessage("error", payload.message);
  else if (event === "done") console.info("turn cost $", payload.usage?.cost_usd, payload.usage);
}

// ---- recipe cards -------------------------------------------------------------------------
function renderRecipes(cards) {
  if (!cards.length) return;
  const grid = el("div", "recipes");
  for (const card of cards) grid.append(recipeCard(card));
  $("messages").append(grid);
  scrollDown();
}

function recipeCard(card) {
  const node = $("recipe-card").content.firstElementChild.cloneNode(true);
  const img = node.querySelector(".recipe-img");
  const image = card.image && safeUrl(card.image);
  if (image) { img.src = image; img.onerror = () => img.remove(); } else img.remove();
  node.querySelector(".recipe-title").textContent = card.title;
  const meta = [card.site, card.minutes ? `${card.minutes} דק'` : null, card.servings].filter(Boolean);
  node.querySelector(".recipe-meta").textContent = meta.join(" · ");
  node.querySelector(".recipe-note").textContent = card.note || "";
  node.querySelector(".recipe-subs").textContent = card.needs_substitute.length
    ? "⚠️ צריך תחליף: " + card.needs_substitute.map((s) => s.ingredient).join(", ")
    : "";
  const missing = node.querySelector(".recipe-missing");
  if (card.missing.length) {
    missing.querySelector("summary").textContent = `חסר לך (${card.missing.length})`;
    for (const item of card.missing) missing.querySelector("ul").append(el("li", "", item));
  } else {
    missing.replaceWith(el("div", "recipe-meta", "✓ יש לך הכול"));
  }
  const link = node.querySelector(".recipe-link");
  const url = safeUrl(card.url);
  if (url) link.href = url; else link.remove();
  const rating = node.querySelector(".rating");
  node.querySelector(".recipe-cooked").addEventListener("click", (e) => { e.currentTarget.hidden = true; rating.hidden = false; });
  rating.querySelectorAll("button").forEach((btn) => btn.addEventListener("click", async () => {
    try {
      await api("/api/cooked", { method: "POST", body: JSON.stringify({ title: card.title, url: card.url, rating: btn.dataset.rating }) });
      rating.replaceChildren(el("span", "", `נשמר ${RATING_LABELS[btn.dataset.rating]} תודה!`));
      refreshSidebar();
    } catch { rating.replaceChildren(el("span", "", "לא הצלחתי לשמור")); }
  }));
  return node;
}

// ---- sidebar: profile, pantry, cooked -----------------------------------------------------
function chip(text, onRemove) {
  const node = el("span", "chip", text);
  const x = el("button", "", "×");
  x.type = "button";
  x.title = "מחיקה";
  x.setAttribute("aria-label", `מחיקת ${text}`);
  x.addEventListener("click", onRemove);
  node.append(x);
  return node;
}

function renderProfile(p) {
  const box = $("profile");
  box.replaceChildren();
  let any = false;
  for (const field of Object.keys(KIND_LABELS)) {
    const values = field === "diet" ? (p.diet ? [p.diet] : []) : p[field];
    if (!values.length) continue;
    any = true;
    const group = el("div", "pref-group");
    group.append(el("div", "pref-label", KIND_LABELS[field]));
    const chips = el("div", "chips");
    for (const v of values) {
      chips.append(chip(v, async () => {
        renderProfile(await api("/api/profile/forget", { method: "POST", body: JSON.stringify({ kind: KIND_OF_FIELD[field], value: v }) }));
      }));
    }
    group.append(chips);
    box.append(group);
  }
  if (!any) box.append(el("div", "empty", "עוד לא נשמר כלום. אפשר לכתוב בצ'אט, למשל: \"אני אלרגית לאגוזים\"."));
  renderCooked(p.cooked);
}

function renderCooked(items) {
  const list = $("cooked");
  list.replaceChildren();
  if (!items.length) { list.append(el("li", "empty", "עוד לא סימנת \"הכנתי\" על מתכון.")); return; }
  for (const c of items) {
    const li = el("li");
    const url = c.url && safeUrl(c.url);
    const title = url ? Object.assign(el("a", "", c.title), { href: url, target: "_blank", rel: "noopener noreferrer" }) : el("span", "", c.title);
    li.append(title, el("span", "", ` ${c.rating ? RATING_LABELS[c.rating] : ""} · ${c.cooked_on}`));
    list.append(li);
  }
}

function renderPantry(items) {
  const box = $("pantry");
  box.replaceChildren();
  if (!items.length) { box.append(el("div", "empty", "לא ידוע מה יש בבית. אפשר לכתוב בצ'אט או להוסיף כאן.")); return; }
  for (const item of items) {
    box.append(chip(item, () => updatePantry({ remove: [item] })));
  }
}

async function ensureConversation() {
  if (!conversationId) saveConversation((await api("/api/conversations", { method: "POST" })).conversation_id);
  return conversationId;
}

async function updatePantry(change) {
  const id = await ensureConversation();
  renderPantry((await api(`/api/conversations/${id}/pantry`, { method: "POST", body: JSON.stringify(change) })).items);
}

async function refreshSidebar() {
  try {
    renderProfile(await api("/api/profile"));
    renderPantry(conversationId ? (await api(`/api/conversations/${conversationId}/pantry`)).items : []);
  } catch (err) { console.error(err); }
}

// ---- wiring ------------------------------------------------------------------------------
$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("input").value.trim();
  $("input").value = "";
  send(text);
});
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("composer").requestSubmit(); }
});
document.querySelectorAll("#suggestions .chip-btn").forEach((b) => b.addEventListener("click", () => send(b.textContent)));
$("new-chat").addEventListener("click", () => {
  if (busy) return;
  saveConversation(null);
  $("messages").replaceChildren();
  addMessage("agent", GREETING);
  refreshSidebar();
});
$("add-pref").addEventListener("submit", async (e) => {
  e.preventDefault();
  const value = $("pref-value").value.trim();
  if (!value) return;
  try {
    renderProfile(await api("/api/profile/remember", { method: "POST", body: JSON.stringify({ kind: $("pref-kind").value, value }) }));
    $("pref-value").value = "";
  } catch { addMessage("error", "לא הצלחתי לשמור בפרופיל."); }
});
$("add-pantry").addEventListener("submit", async (e) => {
  e.preventDefault();
  const value = $("pantry-value").value.trim();
  if (!value) return;
  await updatePantry({ add: [value] });
  $("pantry-value").value = "";
});

async function start() {
  addMessage("agent", GREETING);
  try {
    const health = await api("/api/health");
    if (!health.api_key_configured) {
      $("banner").textContent = "⚠️ חסר מפתח API: הצ'אט לא יעבוד עד שתוסיפי אותו לקובץ .env ותפעילי מחדש את השרת. הפרופיל והמזווה עובדים גם בלי.";
      $("banner").hidden = false;
    }
  } catch { /* server down: the first send will say so */ }
  if (conversationId) {
    try { renderRecipes(await api(`/api/conversations/${conversationId}/recipes`)); } catch { /* old id */ }
  }
  refreshSidebar();
}
start();
