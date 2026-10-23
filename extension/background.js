const SERVER = "http://127.0.0.1:8765";

function money(n) { return "$" + Math.round(n).toLocaleString("en-US"); }

async function capture(page, tabId) {
  let result;
  try {
    const resp = await fetch(SERVER + "/capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(page),
    });
    result = await resp.json().catch(() => ({}));
    if (!resp.ok) result = { error: `car-search hit an error on this page: ${result.error || resp.status}` };
  } catch (err) {
    result = { error: "car-search isn't running. Start it with: car-search serve", detail: String(err) };
  }
  result.url = page.url;
  result.at = Date.now();
  if (tabId !== undefined) {
    await chrome.storage.session.set({ ["tab:" + tabId]: result });
    const text = result.error ? "off" : result.deals?.length ? "$" : result.saved ? String(result.saved) : "";
    chrome.action.setBadgeText({ tabId, text });
    chrome.action.setBadgeBackgroundColor({ tabId, color: result.error ? "#888888" : result.deals?.length ? "#1a7f37" : "#2a78d6" });
  }
  for (const deal of result.deals || []) notify(deal);
  return result;
}

async function notify(deal) {
  const id = "deal:" + deal.vin + ":" + deal.price;
  const pct = (deal.deal_score * 100).toFixed(1);
  chrome.notifications.create(id, {
    type: "basic",
    iconUrl: "icon.png",
    title: `Deal: ${deal.title}`,
    message: `${money(deal.price)} · ${pct}% below market (${money(deal.market_price)}) · ${deal.miles.toLocaleString("en-US")} mi`,
    priority: 2,
  });
  await chrome.storage.session.set({ [id]: deal.url });
}

chrome.notifications.onClicked.addListener(async (id) => {
  const url = (await chrome.storage.session.get(id))[id];
  if (url) chrome.tabs.create({ url });
  chrome.notifications.clear(id);
});

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg.type === "capture") {
    capture(msg.page, sender.tab?.id).then(reply);
    return true;
  }
});
