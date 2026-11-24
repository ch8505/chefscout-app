// ChefScout web page. Plain JavaScript, no build step.
// Model text is always inserted with textContent (never innerHTML), and links come only from recipe cards.
"use strict";

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
// Profile fields, in the order the labels appear on the fridge, with the kind the API expects.
const PROFILE_FIELDS = [
  { field: "allergies", kind: "allergy", label: "אלרגיה" },
  { field: "avoid", kind: "avoid", label: "לעולם לא" },
  { field: "kitchen_rules", kind: "kitchen_rule", label: "כלל מטבח" },
  { field: "diet", kind: "diet", label: "תזונה" },
  { field: "likes", kind: "like", label: "אוהבת" },
];
const RATING_LABELS = { loved: "אהבתי", ok: "בסדר", disliked: "לא בשבילי" };
// Which station of the scout trail each tool belongs to (tool names come from the server's progress event).
const TRAIL_STEP = { web_search: 0, read_recipes: 1, show_recipes: 2 };
// Quick actions on the home screen and under the composer.
const QUICK = {
  idea: { send: "תני לי 3 רעיונות לארוחת ערב" },
  pantry: { send: "מה אפשר להכין ממה שיש לי בבית?", needsPantry: true },
  quick: { send: "משהו מהיר להכין היום, עד 30 דקות" },
  search: { prefill: "אני מחפשת מתכון ל" },
};

let conversationId = loadConversation();
let busy = false;
let pantryItems = [];
let ticketCount = 0;

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
function svgEl(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
  return node;
}
function icon(id, className = "ic") {
  const s = svgEl("svg", { class: className, "aria-hidden": "true" });
  s.append(svgEl("use", { href: `#${id}` }));
  return s;
}
// The spoon character. mood: "happy" | "oops"
function spoon(className, mood = "happy") {
  const s = svgEl("svg", { class: className, viewBox: "0 0 40 48", "aria-hidden": "true" });
  s.append(svgEl("use", { href: "#i-spoon" }));
  for (const cx of [15, 25]) s.append(svgEl("circle", { cx, cy: 15, r: 1.8, fill: "var(--ink)" }));
  const mouth = mood === "oops" ? "M17 22a3 3 0 106 0a3 3 0 10-6 0" : "M15 20c2.5 2.5 7.5 2.5 10 0";
  s.append(svgEl("path", { d: mouth, stroke: "var(--ink)", "stroke-width": 2, fill: "none", "stroke-linecap": "round" }));
  return s;
}
function safeUrl(url) {
  try { const u = new URL(url); return u.protocol === "https:" || u.protocol === "http:" ? u.href : null; } catch { return null; }
}
// "2026-11-24" -> "24.11"
function shortDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
  return m ? `${m[3]}.${m[2]}` : iso || "";
}
function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
function scrollDown() { window.scrollTo({ top: document.body.scrollHeight, behavior: reducedMotion() ? "auto" : "smooth" }); }

let toastTimer;
function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3000);
}

// ---- home screen -----------------------------------------------------------------------
function showHome(on) { $("home").hidden = !on; $("suggestions").hidden = on; }

function runQuick(action) {
  const q = QUICK[action];
  if (!q || busy) return;
  if (q.prefill) { appendToInput(q.prefill, true); return; }
  if (q.needsPantry && !pantryItems.length) {
    toast("קודם ספרי לי מה יש בבית: אפשר להוסיף מגנטים במקרר.");
    setKeep("today");
    openFridge();
    $("item-value").focus();
    return;
  }
  send(q.send);
}

// Put text in the composer: replace it, or add it to what is already written.
function appendToInput(text, replace = false) {
  const input = $("input");
  const current = input.value.trim();
  input.value = replace || !current ? text : `${current}, ${text}`;
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
  autoGrow();
}

// ---- chat ------------------------------------------------------------------------------
function addMessage(role, text) {
  showHome(false);
  let node;
  if (role === "user") {
    node = el("div", "msg user", text);
  } else {
    node = el("div", `msg ${role}`);
    node.append(spoon("av", role === "error" ? "oops" : "happy"), el("div", "bubble", text));
  }
  $("messages").append(node);
  scrollDown();
  return node;
}

// ---- scout trail: live progress from the server's progress events -------------------------
function trailStart() {
  $("trail").hidden = false;
  $("track").hidden = true;
  $("track").style.setProperty("--step", 0);
  document.querySelectorAll(".station").forEach((s) => s.classList.remove("active", "done"));
  $("progress-text").textContent = "חושב…";
  scrollDown();
}
function trailProgress(label, tool) {
  $("progress-text").textContent = label;
  const step = TRAIL_STEP[tool];
  if (step === undefined) return; // memory tools: label only
  $("track").hidden = false;
  $("track").style.setProperty("--step", step);
  document.querySelectorAll(".station").forEach((s, i) => {
    s.classList.toggle("done", i < step);
    s.classList.toggle("active", i === step);
  });
}
function trailStop() { $("trail").hidden = true; }

function setBusy(on) {
  busy = on;
  for (const b of document.querySelectorAll("#send, #new-chat, .quick-btn, #suggestions .chip-btn, .t-chip, .mini-btn")) b.disabled = on;
  if (on) trailStart(); else trailStop();
}

// A fresh id per button press: the server does the same request only once (double clicks, retries).
function requestId() {
  try { return crypto.randomUUID(); } catch { return `r${Date.now()}${Math.random().toString(36).slice(2, 10)}`; }
}

// action (optional): a button on an idea or recipe card, e.g. { type: "get_recipes", meal_id: "M1" }.
async function send(text, action = null) {
  if (busy || !text.trim()) return;
  stopDictation();
  addMessage("user", text);
  setBusy(true);
  try {
    const body = { message: text, conversation_id: conversationId };
    if (action) Object.assign(body, { action, request_id: requestId() });
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
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
  else if (event === "progress") trailProgress(payload.label, payload.tool);
  else if (event === "answer") addMessage("agent", payload.text);
  else if (event === "ideas") renderIdeas(payload);
  else if (event === "recipes") renderRecipes(payload);
  else if (event === "error") addMessage("error", payload.message);
  else if (event === "done") {
    if (payload.duplicate) toast("הבקשה הזו כבר בוצעה.");
    else console.info("turn cost $", payload.usage?.cost_usd, payload.usage);
  }
}

// ---- the menu board: ideas before any recipe (no links here: an idea is not a recipe) ---------
function miniButton(text, label, onClick, className = "") {
  const b = el("button", `mini-btn ${className}`.trim(), text);
  b.type = "button";
  b.disabled = busy;
  if (label) b.setAttribute("aria-label", label);
  b.addEventListener("click", onClick);
  return b;
}

async function sendFeedback(target, feedback) {
  if (busy) return null;
  const id = await ensureConversation();
  return api(`/api/conversations/${id}/feedback`, {
    method: "POST",
    body: JSON.stringify({ target, feedback, request_id: requestId() }),
  });
}

function dishRow(dish, inMeal) {
  const row = el("li", `dish${dish.recipe ? " has-recipe" : ""}`);
  const head = el("div", "d-head");
  head.append(el("span", "d-role", dish.role_label), el("span", "d-name", dish.name));
  const meta = [];
  if (dish.est_minutes) meta.push(`כ-${dish.est_minutes} דק'`);
  if (!inMeal) meta.push(dish.kosher_label);
  if (dish.search_status === "not_found") meta.push("לא נמצא מתכון");
  if (dish.recipe) meta.push("✓ יש מתכון");
  row.append(head);
  if (meta.length) row.append(el("div", "d-meta", meta.join(" · ")));

  const acts = el("div", "d-acts");
  if (!dish.recipe) {
    acts.append(miniButton("מתכון", `מתכון ל${dish.name}`, () =>
      send(`תביא לי מתכון ל${dish.name}`, { type: "get_recipes", dish_id: dish.id }), "go"));
  }
  acts.append(miniButton("החלף", `החלפת ${dish.name}`, () =>
    send(`תחליף את ${dish.name} במשהו אחר`, { type: "replace", dish_id: dish.id })));
  acts.append(miniButton("לא מתאים", `${dish.name} לא מתאים לי`, async () => {
    if (inMeal) { send(`${dish.name} לא מתאים לי`, { type: "reject", dish_id: dish.id }); return; }
    try {
      const plan = await sendFeedback(dish.id, "rejected");
      if (plan) renderIdeas(plan);
      toast(`${dish.name} לא יוצע לך בזמן הקרוב.`);
    } catch { toast("לא הצלחתי לשמור. אפשר לנסות שוב."); }
  }, "quiet"));
  row.append(acts);
  return row;
}

function renderIdeas(plan) {
  if (!plan || (!plan.meals.length && !plan.ideas.length)) return;
  showHome(false);
  // Only the newest board is live; older ones keep their text but lose their buttons.
  document.querySelectorAll(".board:not(.stale)").forEach((b) => {
    b.classList.add("stale");
    b.querySelectorAll("button").forEach((btn) => btn.remove());
  });
  const board = el("section", "board");
  board.setAttribute("aria-label", plan.meals.length ? "הצעות לארוחה" : "רעיונות");
  const cards = el("div", "menus");
  if (plan.meals.length) {
    plan.meals.forEach((meal, i) => {
      const card = el("article", "menu");
      const top = el("div", "m-top");
      top.append(el("span", "m-num", `ארוחה ${i + 1}`), el("span", `m-kosher k-${meal.kosher}`, meal.kosher_label));
      card.append(top, el("h3", "m-title", meal.title));
      const list = el("ul", "dishes");
      for (const dish of meal.dishes) list.append(dishRow(dish, true));
      card.append(list);
      const needRecipe = meal.dishes.some((d) => !d.recipe);
      if (needRecipe) {
        card.append(miniButton("תביא מתכונים לכל הארוחה", `מתכונים לארוחה ${i + 1}`, () =>
          send(`תביא מתכונים לארוחה ${i + 1}: ${meal.title}`, { type: "get_recipes", meal_id: meal.id }), "go wide"));
      }
      cards.append(card);
    });
  } else {
    const card = el("article", "menu");
    card.append(el("div", "m-top", "רעיונות"));
    const list = el("ul", "dishes");
    for (const dish of plan.ideas) list.append(dishRow(dish, false));
    card.append(list);
    cards.append(card);
  }
  board.append(cards, el("p", "board-note", "אלה רעיונות. מתכון אמיתי עם קישור מגיע רק אחרי שבוחרים."));
  $("messages").append(board);
  scrollDown();
}

// ---- the pass: recipe tickets ---------------------------------------------------------------
function renderRecipes(cards) {
  if (!cards.length) return;
  showHome(false);
  const pass = el("section", "pass");
  pass.setAttribute("aria-label", "המתכונים שנמצאו");
  const rail = el("div", "rail");
  rail.setAttribute("aria-hidden", "true");
  const tickets = el("div", "tickets");
  cards.forEach((card, i) => {
    const ticket = recipeTicket(card);
    ticket.classList.add("hang");
    ticket.style.setProperty("--delay", `${i * 0.15}s`);
    tickets.append(ticket);
  });
  pass.append(rail, tickets);
  $("messages").append(pass);
  scrollDown();
}

// A tappable ingredient on a ticket: asks the agent for a verified substitute.
function substituteChip(text, question, isRule) {
  const b = el("button", `t-chip${isRule ? " rule" : ""}`);
  b.type = "button";
  b.disabled = busy;
  b.append(el("span", "", text), icon("i-swap"));
  b.setAttribute("aria-label", `${text}: לשאול במה להחליף`);
  b.addEventListener("click", () => send(question));
  return b;
}

function recipeTicket(card) {
  const node = $("recipe-card").content.firstElementChild.cloneNode(true);
  ticketCount += 1;
  node.querySelector(".t-num").textContent = `#${String(ticketCount).padStart(2, "0")}`;
  node.querySelector(".t-site").textContent = card.site || "";
  if (card.role_label) node.querySelector(".t-top").prepend(el("span", "t-role", card.role_label));
  if (card.alternative) {
    node.classList.add("alt");
    node.querySelector(".t-title").before(el("div", "t-alt", `⏱ ארוך מהמבוקש${card.minutes ? ` (${card.minutes} דק')` : ""}`));
  } else if (card.time_fit === "close") {
    node.querySelector(".t-title").before(el("div", "t-alt soft", "קצת יותר מהזמן שביקשת"));
  }

  const img = node.querySelector(".t-img");
  const image = card.image && safeUrl(card.image);
  if (image) { img.src = image; img.onerror = () => img.remove(); } else img.remove();

  node.querySelector(".t-title").textContent = card.title;
  const meta = node.querySelector(".t-meta");
  if (card.minutes) {
    const time = el("span");
    time.append(el("b", "", String(card.minutes)), document.createTextNode(" דק'"));
    meta.append(time);
  }
  if (card.servings) meta.append(el("span", "", card.servings));
  if (!meta.childElementCount) meta.remove();
  node.querySelector(".t-note").textContent = card.note || "";

  const rule = node.querySelector(".t-rule");
  if (card.needs_substitute.length) {
    for (const s of card.needs_substitute) {
      rule.querySelector(".t-chips").append(substituteChip(s.ingredient,
        `במתכון "${card.title}" יש ${s.ingredient}, וזה נגד כלל המטבח שלי. במה אפשר להחליף?`, true));
    }
  } else rule.remove();

  const missing = node.querySelector(".t-missing");
  if (card.missing.length) {
    missing.querySelector(".t-sec-h").textContent = `חסר לך (${card.missing.length}) · להקיש לתחליף`;
    for (const item of card.missing) {
      missing.querySelector(".t-chips").append(substituteChip(item, `אין לי ${item} בשביל "${card.title}". במה אפשר להחליף?`, false));
    }
    node.querySelector(".t-have").remove();
  } else missing.remove();

  const link = node.querySelector(".recipe-link");
  const url = safeUrl(card.url);
  if (url) link.href = url; else link.remove();

  const actions = node.querySelector(".t-actions");
  const extra = el("div", "t-extra");
  extra.append(miniButton("שמור", `לשמור את ${card.title}`, async (e) => {
    const b = e.currentTarget;
    try { await sendFeedback(card.id, "saved"); b.replaceWith(el("span", "saved", "נשמר ✓")); }
    catch { toast("לא הצלחתי לשמור. אפשר לנסות שוב."); }
  }));
  if (card.dish_id) {
    extra.append(miniButton("עוד אפשרויות", `עוד מתכונים ל${card.role_label || "מנה הזו"}`, () =>
      send(`עוד אפשרויות ל${card.role_label || card.title}`, { type: "more", dish_id: card.dish_id })));
  }
  actions.after(extra);
  const rating = node.querySelector(".rating");
  node.querySelector(".recipe-cooked").addEventListener("click", (e) => {
    e.currentTarget.hidden = true;
    rating.hidden = false;
    rating.querySelector("button").focus();
  });
  rating.querySelectorAll("button").forEach((btn) => btn.addEventListener("click", async () => {
    const value = btn.dataset.rating;
    try {
      await api("/api/cooked", { method: "POST", body: JSON.stringify({ title: card.title, url: card.url, rating: value }) });
      rating.remove();
      actions.replaceChildren(...(url ? [link] : []), el("span", "saved", `נכתב בפנקס · ${RATING_LABELS[value]}`));
      const stamp = el("div", "stamp in", "בושל!");
      stamp.append(el("small", "", shortDate(today())));
      stamp.setAttribute("aria-hidden", "true");
      node.append(stamp);
      refreshSidebar();
    } catch { rating.replaceChildren(el("span", "saved", "לא הצלחתי לשמור. אפשר לנסות שוב.")); }
  }));
  return node;
}

// ---- the fridge: labels (profile), magnets (pantry), notebook (cooked) ----------------------
function removeButton(name, onRemove) {
  const x = el("button", "x", "×");
  x.type = "button";
  x.title = "הסרה";
  x.setAttribute("aria-label", `הסרת ${name}`);
  x.addEventListener("click", onRemove);
  return x;
}

let profileCount = 0;
// The item the user just added, so only it gets the "stick" / "snap" animation.
let justAdded = null; // { where: "label" | "magnet", value }
function isJustAdded(where, value) { return justAdded && justAdded.where === where && justAdded.value === value; }
function updateFridgeCount() { $("fridge-count").textContent = String(profileCount + pantryItems.length); }

function renderProfile(p) {
  const box = $("profile");
  box.replaceChildren();
  profileCount = 0;
  for (const { field, kind, label } of PROFILE_FIELDS) {
    const values = field === "diet" ? (p.diet ? [p.diet] : []) : p[field];
    for (const v of values) {
      profileCount += 1;
      const wrap = el("span", `dymo-wrap${isJustAdded("label", v) ? " is-new" : ""}`);
      const tape = el("span", `dymo ${kind}`);
      tape.append(el("span", "k", label), el("span", "v", v), removeButton(`${label} ${v}`, async () => {
        try {
          renderProfile(await api("/api/profile/forget", { method: "POST", body: JSON.stringify({ kind, value: v }) }));
        } catch { toast("לא הצלחתי להסיר. אפשר לנסות שוב."); }
      }));
      wrap.append(tape);
      box.append(wrap);
    }
  }
  if (!profileCount) box.append(el("span", "empty", "עוד אין מדבקות. אפשר לכתוב בצ'אט, למשל: \"אני אלרגית לאגוזים\"."));
  updateFridgeCount();
  renderCooked(p.cooked);
}

function renderCooked(items) {
  const list = $("cooked");
  list.replaceChildren();
  if (!items.length) { list.append(el("li", "empty", "עוד לא סימנת \"הכנתי\" על מתכון.")); return; }
  for (const c of [...items].reverse()) {
    const li = el("li");
    const url = c.url && safeUrl(c.url);
    const title = url
      ? Object.assign(el("a", "n-title", c.title), { href: url, target: "_blank", rel: "noopener noreferrer" })
      : el("span", "n-title", c.title);
    li.append(title);
    if (c.rating) {
      const face = icon(`face-${c.rating}`, "");
      face.setAttribute("role", "img");
      face.setAttribute("aria-label", RATING_LABELS[c.rating]);
      face.removeAttribute("aria-hidden");
      li.append(face);
    }
    li.append(el("span", "n-date", shortDate(c.cooked_on)));
    list.append(li);
  }
}

function renderPantry(items) {
  pantryItems = items;
  const box = $("pantry");
  box.replaceChildren();
  if (!items.length) box.append(el("span", "empty", "המקרר ריק. מה יש בבית?"));
  for (const item of items) {
    const magnet = el("span", `magnet${isJustAdded("magnet", item) ? " is-new" : ""}`);
    const use = el("button", "m-use", item);
    use.type = "button";
    use.title = "להוסיף להודעה";
    use.setAttribute("aria-label", `להוסיף ${item} להודעה`);
    use.addEventListener("click", () => { appendToInput(item); closeFridgeOnPhone(); });
    magnet.append(use, removeButton(item, () => updatePantry({ remove: [item] }).catch(() => toast("לא הצלחתי להוריד. אפשר לנסות שוב."))));
    box.append(magnet);
  }
  updateFridgeCount();
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

// "Just for today" (magnet) or "forever" (label): one form, an explicit choice.
function keepValue() { return document.querySelector('input[name="keep"]:checked').value; }
function setKeep(value) {
  document.querySelector(`input[name="keep"][value="${value}"]`).checked = true;
  onKeepChange();
}
function onKeepChange() {
  const forever = keepValue() === "forever";
  $("pref-kind").hidden = !forever;
  $("item-value").maxLength = forever ? 100 : 60;
  $("item-value").placeholder = forever ? "למשל: אגוזים" : "מה יש בבית? למשל: ביצים";
}
document.querySelectorAll('input[name="keep"]').forEach((r) => r.addEventListener("change", onKeepChange));

$("add-item").addEventListener("submit", async (e) => {
  e.preventDefault();
  const value = $("item-value").value.trim();
  if (!value) return;
  try {
    if (keepValue() === "forever") {
      justAdded = { where: "label", value };
      renderProfile(await api("/api/profile/remember", { method: "POST", body: JSON.stringify({ kind: $("pref-kind").value, value }) }));
    } else {
      justAdded = { where: "magnet", value };
      await updatePantry({ add: [value] });
    }
    $("item-value").value = "";
  } catch { toast("לא הצלחתי לשמור. אפשר לנסות שוב."); }
  finally { justAdded = null; }
});

// ---- the fridge as a bottom sheet on phones ------------------------------------------------
const phone = window.matchMedia("(max-width: 860px)");
let focusBeforeSheet = null;
function sheetOpen() { return $("fridge").classList.contains("open"); }
function openFridge() {
  if (!phone.matches) { $("item-value").focus(); return; }
  focusBeforeSheet = document.activeElement;
  $("fridge").classList.add("open");
  $("fridge").setAttribute("role", "dialog");
  $("fridge").setAttribute("aria-modal", "true");
  $("scrim").classList.add("on");
  document.body.classList.add("sheet-open");
  $("fridge-toggle").setAttribute("aria-expanded", "true");
  $("fridge-close").focus();
}
function closeFridge(restoreFocus = true) {
  if (!sheetOpen()) return;
  $("fridge").classList.remove("open");
  $("fridge").removeAttribute("role");
  $("fridge").removeAttribute("aria-modal");
  $("scrim").classList.remove("on");
  document.body.classList.remove("sheet-open");
  $("fridge-toggle").setAttribute("aria-expanded", "false");
  if (restoreFocus && focusBeforeSheet && document.contains(focusBeforeSheet)) focusBeforeSheet.focus();
}
function closeFridgeOnPhone() { if (phone.matches) closeFridge(false); }
$("fridge-toggle").addEventListener("click", () => (sheetOpen() ? closeFridge() : openFridge()));
$("fridge-close").addEventListener("click", () => closeFridge());
$("scrim").addEventListener("click", () => closeFridge());
phone.addEventListener("change", () => closeFridge(false));
document.addEventListener("keydown", (e) => {
  if (!sheetOpen()) return;
  if (e.key === "Escape") { closeFridge(); return; }
  if (e.key !== "Tab") return; // keep keyboard focus inside the open sheet
  const focusable = [...$("fridge").querySelectorAll("button, input, select, a[href]")].filter((n) => !n.disabled && n.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0], last = focusable[focusable.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});

// ---- voice dictation (only where the browser supports Hebrew speech recognition) ------------
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognizer = null;
function setupDictation() {
  if (!Recognition) return; // the mic button stays hidden
  const mic = $("mic");
  mic.hidden = false;
  mic.title = "הכתבה קולית (הדפדפן שולח את ההקלטה לשירות זיהוי הדיבור שלו)";
  mic.addEventListener("click", () => (recognizer ? stopDictation() : startDictation()));
}
function startDictation() {
  const input = $("input");
  const before = input.value.trim();
  try {
    recognizer = new Recognition();
  } catch { toast("ההכתבה לא זמינה בדפדפן הזה."); return; }
  recognizer.lang = "he-IL";
  recognizer.interimResults = true;
  recognizer.continuous = false;
  recognizer.onresult = (e) => {
    const heard = Array.from(e.results, (r) => r[0].transcript).join(" ");
    input.value = (before ? before + " " : "") + heard;
    autoGrow();
  };
  recognizer.onerror = (e) => {
    const msg = e.error === "not-allowed" || e.error === "service-not-allowed"
      ? "אין הרשאה למיקרופון. אפשר לאשר אותה בהגדרות הדפדפן."
      : e.error === "no-speech" ? "לא שמעתי כלום. אפשר לנסות שוב." : "ההכתבה לא עבדה כרגע. אפשר להקליד.";
    toast(msg);
  };
  recognizer.onend = () => { recognizer = null; $("mic").setAttribute("aria-pressed", "false"); input.focus(); };
  try {
    recognizer.start();
    $("mic").setAttribute("aria-pressed", "true");
  } catch { recognizer = null; toast("ההכתבה לא עבדה כרגע. אפשר להקליד."); }
}
function stopDictation() { if (recognizer) recognizer.stop(); }

// ---- wiring ------------------------------------------------------------------------------
function autoGrow() {
  const t = $("input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 160) + "px";
}
$("input").addEventListener("input", autoGrow);
$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("input").value.trim();
  $("input").value = "";
  autoGrow();
  send(text);
});
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("composer").requestSubmit(); }
});
document.querySelectorAll("[data-action]").forEach((b) => b.addEventListener("click", () => runQuick(b.dataset.action)));
$("new-chat").addEventListener("click", async () => {
  if (busy) return;
  // The magnets fall off the fridge: today's pantry belongs to the old conversation.
  const magnets = [...document.querySelectorAll("#pantry .magnet")];
  const visible = magnets.length && $("fridge").getBoundingClientRect().width > 0 && (!phone.matches || sheetOpen());
  if (visible && !reducedMotion()) {
    magnets.forEach((m, i) => { m.style.setProperty("--delay", `${i * 0.06}s`); m.classList.add("drop"); });
    await new Promise((r) => setTimeout(r, 700 + magnets.length * 60));
  }
  saveConversation(null);
  $("messages").replaceChildren();
  ticketCount = 0;
  showHome(true);
  refreshSidebar();
  toast("שיחה חדשה: המגנטים ירדו, המדבקות נשארו.");
});

async function start() {
  showHome(true);
  setupDictation();
  onKeepChange();
  try {
    const health = await api("/api/health");
    if (!health.api_key_configured) {
      $("banner").textContent = "⚠️ חסר מפתח API: הצ'אט לא יעבוד עד שתוסיפי אותו לקובץ .env ותפעילי מחדש את השרת. המקרר והפנקס עובדים גם בלי.";
      $("banner").hidden = false;
    }
  } catch { /* server down: the first send will say so */ }
  if (conversationId) {
    // After a refresh: the saved ideas first, then the last recipe cards (both come from the server).
    try { renderIdeas(await api(`/api/conversations/${conversationId}/plan`)); } catch { /* old id */ }
    try { renderRecipes(await api(`/api/conversations/${conversationId}/recipes`)); } catch { /* old id */ }
  }
  refreshSidebar();
}
start();
