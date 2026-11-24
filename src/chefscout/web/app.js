// Dishly web page. Plain JavaScript, no build step.
// Model text is always inserted with textContent (never innerHTML), and links come only from recipe cards.
"use strict";

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
// Profile groups in the "my kitchen" drawer, with the kind the API expects.
const PROFILE_FIELDS = [
  { field: "allergies", kind: "allergy", label: "אלרגיות" },
  { field: "avoid", kind: "avoid", label: "לעולם לא" },
  { field: "kitchen_rules", kind: "kitchen_rule", label: "כללי מטבח" },
  { field: "diet", kind: "diet", label: "תזונה" },
  { field: "likes", kind: "like", label: "אוהבת" },
];
const RATING_LABELS = { loved: "אהבתי", ok: "בסדר", disliked: "לא בשבילי" };
// Progress labels come from the server; these tools also show placeholder cards while they run.
const SEARCH_TOOLS = new Set(["web_search", "read_recipes", "show_recipes"]);
const STARTERS = {
  idea: { send: "תני לי 3 רעיונות לארוחת ערב" },
  pantry: { send: "מה אפשר להכין ממה שיש לי בבית?", needsPantry: true },
  quick: { send: "משהו מהיר להכין היום, עד 30 דקות" },
  search: { prefill: "מתכון ל" },
};
const KEY = "dishly.conversation";

let conversationId = loadConversation();
let busy = false;
let pantryItems = [];
let replyTo = null; // { dish_id, title }: the next message asks for another version of this dish
let justAdded = null; // { where: "chip" | "pref", value }: only it gets the "pop" animation

// ---- small helpers -------------------------------------------------------------------------------------
function loadConversation() {
  try { return localStorage.getItem(KEY) || localStorage.getItem("chefscout.conversation") || null; } catch { return null; }
}
function saveConversation(id) {
  conversationId = id;
  try { id ? localStorage.setItem(KEY, id) : localStorage.removeItem(KEY); localStorage.removeItem("chefscout.conversation"); } catch { /* private mode */ }
}
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function icon(id, className = "ic") {
  const s = document.createElementNS(SVG_NS, "svg");
  s.setAttribute("class", className);
  s.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `#${id}`);
  s.append(use);
  return s;
}
function button(className, text, onClick, label) {
  const b = el("button", className, text);
  b.type = "button";
  if (label) b.setAttribute("aria-label", label);
  b.addEventListener("click", onClick);
  return b;
}
function safeUrl(url) {
  try { const u = new URL(url); return u.protocol === "https:" || u.protocol === "http:" ? u.href : null; } catch { return null; }
}
function shortDate(iso) { // "2026-11-24" -> "24.11"
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
  return m ? `${m[3]}.${m[2]}` : iso || "";
}
async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
function post(path, body) { return api(path, { method: "POST", body: JSON.stringify(body) }); }
function requestId() {
  try { return crypto.randomUUID(); } catch { return `r${Date.now()}${Math.random().toString(36).slice(2, 10)}`; }
}
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
function scrollDown() { window.scrollTo({ top: document.body.scrollHeight, behavior: reducedMotion() ? "auto" : "smooth" }); }
// Model text is plain text: drop markdown marks (**bold**, # headings, `code`) if any slip through.
function plain(text) { return String(text || "").replace(/\*\*|__|`/g, "").replace(/^#{1,6}\s+/gm, ""); }

let toastTimer;
function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3200);
}
async function ensureConversation() {
  if (!conversationId) saveConversation((await post("/api/conversations", {})).conversation_id);
  return conversationId;
}
const convPath = async (rest) => `/api/conversations/${await ensureConversation()}/${rest}`;

// ---- home ------------------------------------------------------------------------------------------------
function showHome(on) { $("home").hidden = !on; }

function runStarter(name) {
  const s = STARTERS[name];
  if (!s || busy) return;
  if (s.prefill) { setInput(s.prefill); return; }
  if (s.needsPantry && !pantryItems.length) {
    toast("קודם ספרי לי מה יש בבית: הוסיפי למטה, ב\"בבית היום\".");
    openPantryAdd();
    return;
  }
  send(s.send);
}

function setInput(text) {
  const input = $("input");
  input.value = text;
  input.focus();
  input.setSelectionRange(text.length, text.length);
  autoGrow();
}
function appendToInput(text) {
  const current = $("input").value.trim();
  setInput(current ? `${current}, ${text}` : text);
}

// ---- messages --------------------------------------------------------------------------------------------
function addMessage(role, text) {
  showHome(false);
  const node = el("div", `msg ${role}`);
  if (role === "user") {
    node.append(el("div", "text", text));
  } else {
    const avatar = el("span", "avatar");
    avatar.append(icon("i-bowl"));
    const body = el("div", "text");
    body.append(el("p", "say", plain(text)));
    node.append(avatar, body);
  }
  $("messages").append(node);
  return node;
}

// The newest agent message (after a refresh there may be none to attach a list to).
function lastAgentText() {
  const last = $("messages").lastElementChild;
  if (last && last.classList.contains("agent")) return last.querySelector(".text");
  return addMessage("agent", "ההצעות:").querySelector(".text");
}

// ---- working state: one progress line, placeholder cards while searching ------------------------------------
function setBusy(on) {
  busy = on;
  for (const b of document.querySelectorAll("#send, #new-chat, .starter, .go, .have-btn, .sub-btn, .r-other summary, .other-more, .other-variant")) {
    if (!b.classList.contains("replaced")) b.disabled = on;
  }
  $("working").hidden = !on;
  $("skeletons").hidden = true;
  $("working-text").textContent = "חושב…";
  if (on) scrollDown();
}
function progress(label, tool) {
  $("working-text").textContent = label;
  if (SEARCH_TOOLS.has(tool) && $("skeletons").hidden) { $("skeletons").hidden = false; scrollDown(); }
}

// ---- chat turn -------------------------------------------------------------------------------------------
// action (optional): a button, e.g. { type: "get_recipes", dish_id: "D1" } or { type: "variant", dish_id }.
async function send(text, action = null) {
  if (busy || !text.trim()) return;
  stopDictation();
  addMessage("user", text);
  setBusy(true);
  try {
    const body = { message: text, conversation_id: conversationId };
    if (action) Object.assign(body, { action, request_id: requestId() });
    const res = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
    await readEvents(res.body);
  } catch (err) {
    addMessage("error", "לא הצלחתי להתחבר לשרת. האם הוא עדיין רץ?");
    console.error(err);
  } finally {
    setBusy(false);
    refreshPantry();
    scrollDown();
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
  else if (event === "progress") progress(payload.label, payload.tool);
  else if (event === "answer") addMessage("agent", payload.text);
  else if (event === "ideas") renderIdeas(payload);
  else if (event === "recipes") renderCards(payload);
  else if (event === "plan") applyPlan(payload);
  else if (event === "error") addMessage("error", payload.message);
  else if (event === "done") {
    if (payload.duplicate) toast("הבקשה הזו כבר בוצעה.");
    else if (payload.usage) console.info("turn cost $", payload.usage.cost_usd, payload.usage);
  }
}

// ---- ideas: a plain list inside the agent's message; dish names are links ------------------------------------
function dishLink(dish) {
  const b = button("go", dish.name, () => {
    if (b.classList.contains("done")) { showCard(b.dataset.recipe); return; }
    send(`מתכון ל${dish.name}`, { type: "get_recipes", dish_id: dish.id });
  });
  b.dataset.dish = dish.id;
  b.disabled = busy;
  return b;
}

function dishItem(dish, withRole) {
  const li = el("li", "idea");
  if (withRole) li.append(el("span", "idea-role", dish.role_label));
  li.append(dishLink(dish));
  const extra = [];
  if (dish.est_minutes) extra.push(`כ-${dish.est_minutes} דק'`);
  if (!withRole) extra.push(dish.role_label);
  if (extra.length) li.append(el("span", "idea-extra", extra.join(" · ")));
  if (!withRole) li.append(el("span", `tag k-${dish.kosher}`, dish.kosher_label));
  return li;
}

function renderIdeas(plan, into = null) {
  if (!plan || (!plan.meals.length && !plan.ideas.length)) return;
  showHome(false);
  const box = el("div", "ideas");
  if (plan.meals.length) {
    plan.meals.forEach((meal, i) => {
      const block = el("section", "meal");
      const head = el("div", "meal-head");
      head.append(el("h4", "meal-title", `${i + 1}. ${meal.title}`), el("span", `tag k-${meal.kosher}`, meal.kosher_label));
      const all = button("go meal-go", "", () => {
        send(`מתכונים לארוחה ${i + 1}: ${meal.title}`, { type: "get_recipes", meal_id: meal.id });
      });
      all.append("מתכונים לכל הארוחה", el("span", "arrow", "←"));
      all.dataset.meal = meal.id;
      all.disabled = busy;
      head.append(all);
      const list = el("ul", "idea-list");
      for (const dish of meal.dishes) list.append(dishItem(dish, true));
      block.append(head, list);
      box.append(block);
    });
  } else {
    const list = el("ul", "idea-list");
    for (const dish of plan.ideas) list.append(dishItem(dish, false));
    box.append(list);
  }
  (into || lastAgentText()).append(box);
  applyPlan(plan);
  scrollDown();
}

// The plan's current state, applied to every list in the chat: has a recipe / replaced.
function applyPlan(plan) {
  if (!plan) return;
  const dishes = new Map();
  for (const m of plan.meals) for (const d of m.dishes) dishes.set(d.id, d);
  for (const d of plan.ideas) dishes.set(d.id, d);
  for (const link of document.querySelectorAll(".go[data-dish]")) {
    const d = dishes.get(link.dataset.dish);
    if (!d) {
      link.classList.add("replaced");
      link.disabled = true;
      link.title = "המנה הזו הוחלפה";
    } else if (d.recipe) {
      link.classList.add("done");
      link.dataset.recipe = d.recipe.id;
      link.title = "יש מתכון: להקיש כדי לראות אותו";
    }
  }
  for (const link of document.querySelectorAll(".go[data-meal]")) {
    const meal = plan.meals.find((m) => m.id === link.dataset.meal);
    if (!meal) { link.remove(); continue; }
    if (meal.dishes.length && meal.dishes.every((d) => d.recipe)) link.remove();
  }
}

function showCard(recipeId) {
  const cards = [...document.querySelectorAll(`.rcard[data-recipe="${CSS.escape(recipeId || "")}"]`)];
  const card = cards[cards.length - 1];
  if (card) card.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "center" });
}

// ---- recipe cards -----------------------------------------------------------------------------------------
function renderCards(cards) {
  if (!cards || !cards.length) return;
  showHome(false);
  const list = el("section", "cards");
  list.setAttribute("aria-label", "מתכונים");
  for (const card of cards) list.append(recipeCard(card));
  $("messages").append(list);
  scrollDown();
}

function visibleCardIds() {
  return [...new Set([...document.querySelectorAll(".rcard[data-recipe]")].map((n) => n.dataset.recipe))].slice(-20);
}

function servingsText(s) { return /^\d+$/.test(String(s).trim()) ? `${s} מנות` : String(s); }

function recipeCard(card) {
  const node = $("recipe-card").content.firstElementChild.cloneNode(true);
  node.dataset.recipe = card.id;
  if (card.dish_id) node.dataset.dish = card.dish_id;

  const tab = node.querySelector(".r-tab");
  tab.textContent = card.role_label || "";
  const img = node.querySelector(".r-img");
  const image = card.image && safeUrl(card.image);
  if (image) {
    img.src = image;
    img.onerror = () => img.remove();
    img.onload = () => { if (img.naturalWidth < 200) img.remove(); }; // tiny thumbnails look broken
  } else img.remove();

  node.querySelector(".r-site").textContent = card.site || "";
  node.querySelector(".r-title").textContent = card.title;
  const meta = node.querySelector(".r-meta");
  if (card.minutes) { const s = el("span"); s.append(icon("i-clock"), `${card.minutes} דק'`); meta.append(s); }
  if (card.servings) meta.append(el("span", "", servingsText(card.servings)));
  if (card.alternative) { node.classList.add("alt"); meta.append(el("span", "r-warn", "ארוך מהזמן שביקשת")); }
  else if (card.time_fit === "close") meta.append(el("span", "r-warn", "קצת מעבר לזמן"));
  if (!meta.childElementCount) meta.remove();

  const note = node.querySelector(".r-note");
  if (card.note) note.querySelector("span").textContent = card.note; else note.remove();

  const rule = node.querySelector(".r-rule");
  if (card.needs_substitute.length) {
    for (const s of card.needs_substitute) {
      rule.querySelector(".r-items").append(ingredientRow(s.ingredient, [
        subButton(`במתכון "${card.title}" יש ${s.ingredient}, וזה נגד כלל המטבח שלי. במה להחליף?`),
      ]));
    }
  } else rule.remove();

  fillMissing(node, card);

  const link = node.querySelector(".recipe-link");
  const url = safeUrl(card.url);
  if (url) link.href = url; else link.remove();

  const save = node.querySelector(".recipe-save");
  save.addEventListener("click", async () => {
    try {
      await post(await convPath("feedback"), { target: card.id, feedback: "saved", request_id: requestId() });
      const done = el("span", "saved"); done.append(icon("i-check"), "נשמר");
      save.replaceWith(done);
    } catch { toast("לא הצלחתי לשמור. אפשר לנסות שוב."); }
  });

  const other = node.querySelector(".r-other");
  if (card.dish_id) {
    other.querySelector(".other-more").addEventListener("click", () => { other.open = false; moreOptions(card); });
    other.querySelector(".other-variant").addEventListener("click", () => { other.open = false; startVariant(card); });
  } else other.remove();

  const actions = node.querySelector(".r-actions");
  const rating = node.querySelector(".rating");
  node.querySelector(".recipe-cooked").addEventListener("click", (e) => {
    e.currentTarget.hidden = true;
    rating.hidden = false;
    rating.querySelector("button").focus();
  });
  rating.querySelectorAll("button").forEach((btn) => btn.addEventListener("click", async () => {
    const value = btn.dataset.rating;
    try {
      await post("/api/cooked", { title: card.title, url: card.url, rating: value });
      rating.remove();
      const done = el("span", "saved"); done.append(icon("i-check"), `נכתב בפנקס · ${RATING_LABELS[value]}`);
      actions.replaceChildren(...(url ? [link] : []), done);
      refreshProfile();
    } catch { rating.replaceChildren(el("span", "saved", "לא הצלחתי לשמור. אפשר לנסות שוב.")); }
  }));
  return node;
}

function ingredientRow(text, actions) {
  const li = el("li", "r-item");
  const acts = el("span", "acts");
  acts.append(...actions);
  li.append(el("span", "what", text), acts);
  return li;
}
function subButton(question) {
  const b = button("sub-btn", "במה להחליף?", () => send(question));
  b.disabled = busy;
  return b;
}

// "Missing" list: folded; each item -> "I have it" (pantry, no model) or "what instead?" (asks the agent).
function fillMissing(node, card) {
  const missing = node.querySelector(".r-missing");
  const have = node.querySelector(".r-have");
  if (!missing || !have) return;
  const items = missing.querySelector(".r-items");
  items.replaceChildren();
  missing.hidden = !card.missing.length;
  have.hidden = !!card.missing.length;
  missing.querySelector("summary").textContent =
    card.missing.length === 1 ? "חסר לך מצרך אחד" : `חסרים לך ${card.missing.length} מצרכים`;
  for (const line of card.missing) {
    const yes = button("have-btn", "יש לי", () => iHaveIt(line, yes), `יש לי ${line}`);
    yes.disabled = busy;
    items.append(ingredientRow(line, [yes, subButton(`אין לי ${line} בשביל "${card.title}". במה אפשר להחליף?`)]));
  }
}

async function iHaveIt(line, btn) {
  btn.disabled = true;
  try {
    const res = await post(await convPath("have"), { line, recipe_ids: visibleCardIds() });
    justAdded = { where: "chip", value: res.item };
    renderPantry(res.pantry);
    justAdded = null;
    const fresh = new Map(res.cards.map((c) => [c.id, c]));
    for (const node of document.querySelectorAll(".rcard[data-recipe]")) {
      const c = fresh.get(node.dataset.recipe);
      if (c) fillMissing(node, c);
    }
    toast(`${res.item} נוסף ל"בבית היום"`);
  } catch { btn.disabled = false; toast("לא הצלחתי לעדכן. אפשר לנסות שוב."); }
}

async function moreOptions(card) {
  if (busy) return;
  try {
    const res = await post(await convPath("more"), { dish_id: card.dish_id, request_id: requestId() });
    if (res.cards.length) { renderCards(res.cards); return; } // no model call
    if (res.needs_search) send(`עוד מתכון במקום "${card.title}"`, { type: "more", dish_id: card.dish_id });
    else if (res.duplicate) toast("הבקשה הזו כבר בוצעה.");
  } catch { toast("לא הצלחתי להביא עוד מתכון. אפשר לנסות שוב."); }
}

// "Another version": the next message says how ("בטיבול אחר"); the dish keeps its place in the plan.
function startVariant(card) {
  replyTo = { dish_id: card.dish_id, title: card.title };
  $("reply-to-text").textContent = `גרסה אחרת ל"${card.title}"`;
  $("reply-to").hidden = false;
  $("input").placeholder = "איך? למשל: בטיבול אחר, בלי טיגון, טבעוני";
  $("input").focus();
}
function clearReplyTo() {
  replyTo = null;
  $("reply-to").hidden = true;
  $("input").placeholder = "מה בא לך היום?";
}

// ---- today's pantry (in the dock) ---------------------------------------------------------------------------
function renderPantry(items) {
  pantryItems = items;
  const box = $("pantry");
  box.replaceChildren();
  if (!items.length) box.append(el("span", "empty", "עוד לא סיפרת"));
  for (const item of items) {
    const chip = el("span", `chip${justAdded && justAdded.where === "chip" && justAdded.value === item ? " is-new" : ""}`);
    const use = button("use", item, () => appendToInput(item), `להוסיף ${item} להודעה`);
    const x = button("x", "", () => updatePantry({ remove: [item] }).catch(() => toast("לא הצלחתי להוריד. אפשר לנסות שוב.")), `להוריד ${item}`);
    x.append(icon("i-x"));
    chip.append(use, x);
    box.append(chip);
  }
}
async function updatePantry(change) {
  renderPantry((await post(await convPath("pantry"), change)).items);
}
async function refreshPantry() {
  try { renderPantry(conversationId ? (await api(`/api/conversations/${conversationId}/pantry`)).items : []); }
  catch (err) { console.error(err); }
}
function openPantryAdd() {
  $("pantry-add").hidden = false;
  $("pantry-open").hidden = true;
  $("pantry-input").focus();
}
function closePantryAdd() {
  $("pantry-add").hidden = true;
  $("pantry-open").hidden = false;
  $("pantry-input").value = "";
}
$("pantry-open").addEventListener("click", openPantryAdd);
$("pantry-input").addEventListener("keydown", (e) => { if (e.key === "Escape") closePantryAdd(); });
$("pantry-add").addEventListener("submit", async (e) => {
  e.preventDefault();
  const value = $("pantry-input").value.trim();
  if (!value) { closePantryAdd(); return; }
  try {
    justAdded = { where: "chip", value };
    await updatePantry({ add: [value] });
    $("pantry-input").value = "";
    $("pantry-input").focus();
  } catch { toast("לא הצלחתי לשמור. אפשר לנסות שוב."); }
  finally { justAdded = null; }
});

// ---- "my kitchen" drawer: profile (lasting) and the notebook ----------------------------------------------------
function renderProfile(p) {
  const box = $("profile");
  box.replaceChildren();
  let count = 0;
  for (const { field, kind, label } of PROFILE_FIELDS) {
    const values = field === "diet" ? (p.diet ? [p.diet] : []) : p[field];
    if (!values.length) continue;
    count += values.length;
    const group = el("section", "pgroup");
    const chips = el("div", "chips");
    for (const v of values) {
      const chip = el("span", `pchip ${kind}${justAdded && justAdded.where === "pref" && justAdded.value === v ? " is-new" : ""}`);
      const x = button("x", "", async () => {
        try { renderProfile(await post("/api/profile/forget", { kind, value: v })); }
        catch { toast("לא הצלחתי להסיר. אפשר לנסות שוב."); }
      }, `הסרת ${v}`);
      x.append(icon("i-x"));
      chip.append(el("span", "", v), x);
      chips.append(chip);
    }
    group.append(el("h3", "", label), chips);
    box.append(group);
  }
  if (!count) box.append(el("p", "empty", "עוד לא שמרת כלום."));
  renderCooked(p.cooked);
}

function renderCooked(items) {
  const list = $("cooked");
  list.replaceChildren();
  if (!items.length) { list.append(el("li", "empty", "עוד לא סימנת \"הכנתי\" על מתכון.")); return; }
  for (const c of [...items].reverse()) {
    const li = el("li");
    const url = c.url && safeUrl(c.url);
    li.append(url
      ? Object.assign(el("a", "n-title", c.title), { href: url, target: "_blank", rel: "noopener noreferrer" })
      : el("span", "n-title", c.title));
    if (c.rating) li.append(el("span", "n-rate", RATING_LABELS[c.rating]));
    li.append(el("span", "n-date", shortDate(c.cooked_on)));
    list.append(li);
  }
}
async function refreshProfile() {
  try { renderProfile(await api("/api/profile")); } catch (err) { console.error(err); }
}

$("add-pref").addEventListener("submit", async (e) => {
  e.preventDefault();
  const value = $("pref-value").value.trim();
  if (!value) return;
  try {
    justAdded = { where: "pref", value };
    renderProfile(await post("/api/profile/remember", { kind: $("pref-kind").value, value }));
    $("pref-value").value = "";
  } catch { toast("לא הצלחתי לשמור. אפשר לנסות שוב."); }
  finally { justAdded = null; }
});

function selectTab(name) {
  for (const t of ["profile", "notebook"]) {
    $(`tab-${t}`).setAttribute("aria-selected", String(t === name));
    $(`panel-${t}`).hidden = t !== name;
  }
}
$("tab-profile").addEventListener("click", () => selectTab("profile"));
$("tab-notebook").addEventListener("click", () => selectTab("notebook"));

let focusBeforeDrawer = null;
function openKitchen() {
  focusBeforeDrawer = document.activeElement;
  refreshProfile();
  $("kitchen").hidden = false;
  $("scrim").hidden = false;
  $("kitchen").setAttribute("role", "dialog");
  $("kitchen").setAttribute("aria-modal", "true");
  $("kitchen-open").setAttribute("aria-expanded", "true");
  $("kitchen-close").focus();
}
function closeKitchen() {
  if ($("kitchen").hidden) return;
  $("kitchen").hidden = true;
  $("scrim").hidden = true;
  $("kitchen-open").setAttribute("aria-expanded", "false");
  if (focusBeforeDrawer && document.contains(focusBeforeDrawer)) focusBeforeDrawer.focus();
}
$("kitchen-open").addEventListener("click", openKitchen);
$("kitchen-close").addEventListener("click", closeKitchen);
$("scrim").addEventListener("click", closeKitchen);
document.addEventListener("keydown", (e) => {
  if ($("kitchen").hidden) {
    if (e.key === "Escape" && replyTo) clearReplyTo();
    return;
  }
  if (e.key === "Escape") { closeKitchen(); return; }
  if (e.key !== "Tab") return; // keep keyboard focus inside the open drawer
  const focusable = [...$("kitchen").querySelectorAll("button, input, select, a[href]")].filter((n) => !n.disabled && n.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0], last = focusable[focusable.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});
// Close an open "another recipe" menu when clicking elsewhere.
document.addEventListener("click", (e) => {
  for (const d of document.querySelectorAll(".r-other[open]")) if (!d.contains(e.target)) d.open = false;
});

// ---- voice dictation (only where the browser supports Hebrew speech recognition) --------------------------------
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
  try { recognizer = new Recognition(); } catch { toast("ההכתבה לא זמינה בדפדפן הזה."); return; }
  recognizer.lang = "he-IL";
  recognizer.interimResults = true;
  recognizer.continuous = false;
  recognizer.onresult = (e) => {
    input.value = (before ? before + " " : "") + Array.from(e.results, (r) => r[0].transcript).join(" ");
    autoGrow();
  };
  recognizer.onerror = (e) => {
    toast(e.error === "not-allowed" || e.error === "service-not-allowed"
      ? "אין הרשאה למיקרופון. אפשר לאשר אותה בהגדרות הדפדפן."
      : e.error === "no-speech" ? "לא שמעתי כלום. אפשר לנסות שוב." : "ההכתבה לא עבדה כרגע. אפשר להקליד.");
  };
  recognizer.onend = () => { recognizer = null; $("mic").setAttribute("aria-pressed", "false"); input.focus(); };
  try { recognizer.start(); $("mic").setAttribute("aria-pressed", "true"); }
  catch { recognizer = null; toast("ההכתבה לא עבדה כרגע. אפשר להקליד."); }
}
function stopDictation() { if (recognizer) recognizer.stop(); }

// ---- composer and wiring -----------------------------------------------------------------------------------------
function autoGrow() {
  const t = $("input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 160) + "px";
}
$("input").addEventListener("input", autoGrow);
$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("input").value.trim();
  if (!text || busy) return;
  $("input").value = "";
  autoGrow();
  const action = replyTo ? { type: "variant", dish_id: replyTo.dish_id } : null;
  const shown = replyTo ? `גרסה אחרת ל"${replyTo.title}": ${text}` : text;
  clearReplyTo();
  send(shown, action);
});
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("composer").requestSubmit(); }
});
$("reply-to-cancel").addEventListener("click", () => { clearReplyTo(); $("input").focus(); });
document.querySelectorAll("[data-action]").forEach((b) => b.addEventListener("click", () => runStarter(b.dataset.action)));
$("new-chat").addEventListener("click", () => {
  if (busy) return;
  saveConversation(null);
  clearReplyTo();
  $("messages").replaceChildren();
  showHome(true);
  renderPantry([]);
  window.scrollTo({ top: 0 });
  toast("שיחה חדשה. מה שבפרופיל נשאר, \"בבית היום\" מתחיל מחדש.");
});

// ---- after a refresh: the whole chat as it was --------------------------------------------------------------------
async function restore() {
  const entries = await api(`/api/conversations/${conversationId}/transcript`);
  if (entries.length) {
    for (const e of entries) {
      if (e.role === "user") addMessage("user", e.text);
      else if (e.role === "error") addMessage("error", e.text);
      else if (e.role === "cards") renderCards(e.cards);
      else {
        const msg = addMessage("agent", e.text);
        if (e.ideas) renderIdeas(e.ideas, msg.querySelector(".text"));
        renderCards(e.cards);
      }
    }
  } else {
    // A conversation from before the transcript existed: its ideas and last cards.
    renderIdeas(await api(`/api/conversations/${conversationId}/plan`));
    renderCards(await api(`/api/conversations/${conversationId}/recipes`));
  }
  applyPlan(await api(`/api/conversations/${conversationId}/plan`));
  window.scrollTo({ top: document.body.scrollHeight });
}

async function start() {
  showHome(true);
  setupDictation();
  try {
    const health = await api("/api/health");
    if (!health.api_key_configured) {
      $("banner").textContent = "חסר מפתח API: הצ'אט לא יעבוד עד שתוסיפי אותו לקובץ .env ותפעילי מחדש את השרת. הפרופיל והפנקס עובדים גם בלי.";
      $("banner").hidden = false;
    }
  } catch { /* server down: the first send will say so */ }
  if (conversationId) {
    try { await restore(); } catch { /* an old or unknown id */ }
  }
  refreshPantry();
  refreshProfile();
}
start();
