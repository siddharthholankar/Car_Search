// Reads car listings on pages you open and hands them to the background worker.
// Nothing is sent unless the page carries vehicle data or a VIN.
(() => {
  const VIN = /\b[A-HJ-NPR-Z0-9]{17}\b/;
  const MAX_TEXT = 200000;
  let lastKey = "";
  let timer = null;

  function snapshot() {
    const jsonld = [...document.querySelectorAll('script[type="application/ld+json"]')]
      .map((s) => s.textContent || "");
    const text = document.body ? document.body.innerText.slice(0, MAX_TEXT) : "";
    return { url: location.href, title: document.title, jsonld, text };
  }

  function hasCars(page) {
    return page.jsonld.some((t) => /vehicleIdentificationNumber|"vin"\s*:/i.test(t)) ||
      VIN.test(page.text.toUpperCase());
  }

  function send(force) {
    const page = snapshot();
    if (!hasCars(page)) return false;
    // Skip resends of an unchanged page (scrolling, ads refreshing).
    const key = page.url + "|" + page.jsonld.join("").length + "|" + page.text.length;
    if (!force && key === lastKey) return true;
    lastKey = key;
    chrome.runtime.sendMessage({ type: "capture", page });
    return true;
  }

  // `restart` pushes a pending scan back (navigation); otherwise a pending scan stands, so
  // pages that never stop changing (carousels, ads) still get captured.
  function schedule(delay, restart) {
    if (timer && !restart) return;
    clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      chrome.storage.local.get({ auto: true }, ({ auto }) => auto && send(false));
    }, delay);
  }

  chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
    if (msg.type === "capture-now") reply({ sent: send(true) });
  });

  // Listing sites load results late and change pages without reloading.
  schedule(2000, true);
  let url = location.href;
  setInterval(() => {
    if (location.href !== url) { url = location.href; schedule(2500, true); }
  }, 1000);
  new MutationObserver(() => schedule(3000, false)).observe(document.documentElement, { childList: true, subtree: true });
})();
