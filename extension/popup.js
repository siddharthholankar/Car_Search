const SERVER = "http://127.0.0.1:8765";
const $ = (id) => document.getElementById(id);
const money = (n) => "$" + Math.round(n).toLocaleString("en-US");
let dashboard = "http://localhost:8501";
let threshold = 0.08;

function render(result) {
  const page = $("page"), list = $("cars");
  list.textContent = "";
  if (!result) { page.textContent = "No cars captured on this page yet."; page.className = "muted"; return; }
  if (result.error) { page.textContent = result.error; page.className = "error"; return; }
  page.className = "";
  page.textContent = result.found
    ? `${result.found} car${result.found === 1 ? "" : "s"} on this page, ${result.saved} match your search` +
      (result.new ? ` (${result.new} new)` : "")
    : "No car listings found on this page.";
  if (result.saved && result.scored < result.min_training_rows) {
    const note = document.createElement("div");
    note.className = "muted";
    note.textContent = `${result.total_active} cars saved so far. Scores firm up once ~${result.min_training_rows} similar cars are collected.`;
    page.appendChild(note);
  }
  for (const car of (result.cars || []).sort((a, b) => (b.deal_score ?? -9) - (a.deal_score ?? -9)).slice(0, 8)) {
    const li = document.createElement("li");
    const score = document.createElement("span");
    score.className = "score" + (car.deal_score >= threshold ? " good" : "");
    score.textContent = car.deal_score == null ? "not scored yet" : `${(car.deal_score * 100).toFixed(1)}% ${car.deal_score >= 0 ? "below" : "above"} market`;
    li.append(`${car.title} · ${money(car.price)}`, document.createElement("br"), score);
    if (car.market_price) li.append(` · est. ${money(car.market_price)}`);
    if (car.negotiable) li.append(" · negotiable");
    list.appendChild(li);
  }
}

async function currentTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function refresh() {
  try {
    const s = await (await fetch(SERVER + "/status")).json();
    dashboard = s.dashboard || dashboard;
    threshold = s.threshold ?? threshold;
    $("server").textContent = `Connected · ${s.total_active} cars saved, ${s.scored} scored`;
  } catch {
    $("server").className = "error";
    $("server").textContent = "car-search isn't running. In a terminal: car-search serve";
  }
  const tab = await currentTab();
  const key = "tab:" + tab.id;
  render((await chrome.storage.session.get(key))[key]);
}

$("capture").onclick = async () => {
  const tab = await currentTab();
  $("page").textContent = "Reading page…";
  try {
    const { sent } = await chrome.tabs.sendMessage(tab.id, { type: "capture-now" });
    if (!sent) { $("page").textContent = "No car listings found on this page."; return; }
    setTimeout(refresh, 1500);
  } catch {
    $("page").textContent = "Reload this page once (it was open before the extension was installed).";
  }
};
$("dashboard").onclick = () => chrome.tabs.create({ url: dashboard });
chrome.storage.local.get({ auto: true }, ({ auto }) => { $("auto").checked = auto; });
$("auto").onchange = (e) => chrome.storage.local.set({ auto: e.target.checked });
refresh();
