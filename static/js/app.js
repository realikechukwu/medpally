// Progressive enhancement for the CSS-only drawer: Escape and navigation close
// it, and the hamburger exposes its open/closed state to assistive tech.
function closeDrawer() {
  var toggle = document.getElementById("drawer-toggle");
  if (!toggle || !toggle.checked) return;
  toggle.checked = false;
  toggle.dispatchEvent(new Event("change"));
}

function initDrawer() {
  var toggle = document.getElementById("drawer-toggle");
  if (!toggle) return;
  toggle.setAttribute("role", "button");
  toggle.setAttribute("aria-expanded", toggle.checked ? "true" : "false");
  toggle.addEventListener("change", function () {
    toggle.setAttribute("aria-expanded", toggle.checked ? "true" : "false");
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeDrawer();
  });
}

// Large-title collapse: once a page's <h1> scrolls up under the sticky bar, the
// bar swaps the brand for that page's title and keeps it there.
function initBarTitle() {
  var bar = document.querySelector(".top-bar");
  var slot = bar && bar.querySelector(".top-bar-title");
  if (!slot) return;

  if (window.medpallyBarTitleObserver) window.medpallyBarTitleObserver.disconnect();

  var heading = document.querySelector(".page-title");
  if (!heading) {
    // Pages with no heading to collapse (the account page) name themselves in
    // the view context, and the title simply stays pinned.
    if (slot.textContent.trim()) bar.classList.add("is-collapsed");
    return;
  }

  slot.textContent = heading.textContent.trim();
  // The negative top margin puts the trigger line at the bottom edge of the
  // bar, so the swap lands exactly as the heading disappears behind it.
  window.medpallyBarTitleObserver = new IntersectionObserver(
    function (entries) {
      bar.classList.toggle("is-collapsed", !entries[0].isIntersecting);
    },
    { rootMargin: "-" + bar.offsetHeight + "px 0px 0px 0px" }
  ).observe(heading);
}

// The specialty preset is a useful group on a long journal list.  Its toggle
// selects every journal in that preset and reflects a partial manual choice.
function initJournalGroupToggles() {
  document.querySelectorAll("[data-journal-group-toggle]").forEach(function (toggle) {
    if (toggle.dataset.journalGroupInitialised) return;
    toggle.dataset.journalGroupInitialised = "true";
    var group = toggle.getAttribute("data-journal-group-toggle");
    var journals = document.querySelectorAll('[data-journal-group="' + group + '"]');
    if (!journals.length) return;

    function syncToggle() {
      var selected = Array.prototype.filter.call(journals, function (journal) {
        return journal.checked;
      }).length;
      toggle.checked = selected === journals.length;
      toggle.indeterminate = selected > 0 && selected < journals.length;
    }

    toggle.addEventListener("change", function () {
      journals.forEach(function (journal) {
        journal.checked = toggle.checked;
      });
      toggle.indeterminate = false;
    });
    journals.forEach(function (journal) {
      journal.addEventListener("change", syncToggle);
    });
    syncToggle();
  });
}

// A flash message is a receipt for something the reader just did, so it takes
// itself away again instead of sitting under the feed for the rest of the
// session.
var MESSAGE_DISMISS_MS = 5000;
var MESSAGE_FADE_MS = 400;

function initFlashMessages() {
  document.querySelectorAll(".messages[data-autodismiss]").forEach(function (list) {
    if (list.dataset.dismissing) return;
    list.dataset.dismissing = "true";
    window.setTimeout(function () {
      list.classList.add("is-dismissed");
      window.setTimeout(function () {
        list.remove();
      }, MESSAGE_FADE_MS);
    }, MESSAGE_DISMISS_MS);
  });
}

function initFeedMenu() {
  if (window.medpallyFeedMenuInitialised) return;
  window.medpallyFeedMenuInitialised = true;
  document.addEventListener("click", function (event) {
    var menu = document.querySelector(".feed-menu");
    if (!menu) return;
    if (menu.open && !menu.contains(event.target)) menu.open = false;
  });
  document.addEventListener("keydown", function (event) {
    var menu = document.querySelector(".feed-menu");
    if (!menu) return;
    if (event.key === "Escape") menu.open = false;
  });
}

// Tab navigation stays progressively enhanced: ordinary links still work if
// JavaScript is unavailable, while signed-in readers get instant, cached swaps
// of the content area.  We deliberately use sessionStorage, not a shared HTTP
// cache: every page contains a reader's personalised feed and saved papers.
var NAVIGATION_CACHE_VERSION = "v1";
var NAVIGATION_CACHE_TTL_MS = 5 * 60 * 1000;
var navigationRequest;

function navigationScope() {
  return document.body.dataset.navigationUser || "";
}

function navigationCacheKey(url) {
  var scope = navigationScope();
  if (!scope) return "";
  return "medpally:navigation:" + NAVIGATION_CACHE_VERSION + ":" + scope + ":" +
    url.pathname + url.search;
}

function safeSessionGet(key) {
  try {
    return window.sessionStorage.getItem(key);
  } catch (error) {
    return null;
  }
}

function safeSessionSet(key, value) {
  try {
    window.sessionStorage.setItem(key, value);
  } catch (error) {
    // Private browsing and a full storage area should not stop navigation.
  }
}

function readCachedPage(url) {
  var key = navigationCacheKey(url);
  var raw = key && safeSessionGet(key);
  if (!raw) return null;
  try {
    var cached = JSON.parse(raw);
    if (!cached.savedAt || Date.now() - cached.savedAt > NAVIGATION_CACHE_TTL_MS) return null;
    return cached;
  } catch (error) {
    return null;
  }
}

function saveCachedPage(url, page) {
  var key = navigationCacheKey(url);
  if (!key || !page) return;
  page.savedAt = Date.now();
  safeSessionSet(key, JSON.stringify(page));
}

// Flash messages are deliberately left out of the cache: "signed in as …"
// reappearing on the way back to the feed reads like a second login.
function cacheableMainMarkup(main) {
  if (!main.querySelector(".messages")) return main.outerHTML;
  var clone = main.cloneNode(true);
  clone.querySelectorAll(".messages").forEach(function (list) {
    list.remove();
  });
  return clone.outerHTML;
}

function pageFromDocument(pageDocument) {
  var main = pageDocument.getElementById("app-main");
  var bottomNav = pageDocument.getElementById("bottom-nav");
  if (!main || !bottomNav) return null;

  var drawerNav = pageDocument.querySelector(".drawer-nav");
  var barTitle = pageDocument.querySelector(".top-bar-title");
  return {
    main: cacheableMainMarkup(main),
    bottomNav: bottomNav.outerHTML,
    drawerNav: drawerNav ? drawerNav.innerHTML : "",
    barTitle: barTitle ? barTitle.textContent : "",
    title: pageDocument.title,
    scrollY: 0,
  };
}

function snapshotCurrentPage() {
  var main = document.getElementById("app-main");
  var bottomNav = document.getElementById("bottom-nav");
  if (!main || !bottomNav) return null;
  var drawerNav = document.querySelector(".drawer-nav");
  var barTitle = document.querySelector(".top-bar-title");
  return {
    main: cacheableMainMarkup(main),
    bottomNav: bottomNav.outerHTML,
    drawerNav: drawerNav ? drawerNav.innerHTML : "",
    barTitle: barTitle ? barTitle.textContent : "",
    title: document.title,
    scrollY: window.scrollY,
  };
}

function cacheCurrentPage() {
  saveCachedPage(new URL(window.location.href), snapshotCurrentPage());
}

function replaceWithMarkup(current, markup) {
  var template = document.createElement("template");
  template.innerHTML = markup.trim();
  var replacement = template.content.firstElementChild;
  if (!replacement) return null;
  current.replaceWith(replacement);
  return replacement;
}

function hydratePage() {
  initBarTitle();
  initJournalGroupToggles();
  initFeedMenu();
  initWeekGroups();
  initFlashMessages();
  applyWeekState();
  if (window.htmx) window.htmx.process(document.getElementById("app-main"));
  document.dispatchEvent(new CustomEvent("medpally:page-change"));
}

function applyPage(page) {
  var currentMain = document.getElementById("app-main");
  var currentBottomNav = document.getElementById("bottom-nav");
  if (!currentMain || !currentBottomNav) return false;

  if (!replaceWithMarkup(currentMain, page.main)) return false;
  replaceWithMarkup(currentBottomNav, page.bottomNav);

  var drawerNav = document.querySelector(".drawer-nav");
  if (drawerNav && page.drawerNav) drawerNav.innerHTML = page.drawerNav;
  var barTitle = document.querySelector(".top-bar-title");
  if (barTitle) barTitle.textContent = page.barTitle || "";
  if (page.title) document.title = page.title;
  hydratePage();
  return true;
}

function showNavigationSkeleton() {
  var main = document.getElementById("app-main");
  if (!main) return;
  main.classList.add("is-page-loading");
  main.setAttribute("aria-busy", "true");
  main.innerHTML =
    '<div class="page-loading" role="status"><span class="visually-hidden">Loading page</span>' +
    '<div class="skeleton skeleton-title"></div><div class="skeleton skeleton-subtitle"></div>' +
    '<div class="skeleton-card"><div class="skeleton skeleton-line"></div><div class="skeleton skeleton-line skeleton-line-short"></div><div class="skeleton skeleton-copy"></div><div class="skeleton skeleton-copy skeleton-copy-short"></div></div>' +
    '<div class="skeleton-card"><div class="skeleton skeleton-line"></div><div class="skeleton skeleton-line skeleton-line-short"></div><div class="skeleton skeleton-copy"></div></div></div>';
}

function clearNavigationSkeleton() {
  var main = document.getElementById("app-main");
  if (main) {
    main.classList.remove("is-page-loading");
    main.removeAttribute("aria-busy");
  }
}

function restoreScroll(scrollY) {
  window.requestAnimationFrame(function () {
    window.scrollTo(0, Number.isFinite(scrollY) ? scrollY : 0);
  });
}

async function fetchPage(url, signal) {
  var response = await window.fetch(url.href, {
    credentials: "same-origin",
    cache: "no-store",
    signal: signal,
    headers: { "X-MedPally-Navigation": "1" },
  });
  if (response.redirected || response.url !== url.href) {
    window.location.assign(response.url);
    return null;
  }
  if (!response.ok) throw new Error("Navigation request failed");
  var html = await response.text();
  var page = pageFromDocument(new DOMParser().parseFromString(html, "text/html"));
  if (!page) throw new Error("Navigation response is missing page content");
  return page;
}

function isModifiedNavigation(event) {
  return event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey ||
    event.shiftKey || event.altKey;
}

async function navigateTo(url, options) {
  var settings = options || {};
  if (!navigationScope()) {
    window.location.assign(url.href);
    return;
  }
  if (url.href === window.location.href && !settings.fromHistory) return;

  if (!settings.fromHistory) cacheCurrentPage();
  if (navigationRequest) navigationRequest.abort();
  var request = new AbortController();
  navigationRequest = request;

  if (settings.push) window.history.pushState({}, "", url.href);
  var cached = readCachedPage(url);
  if (cached && applyPage(cached)) {
    restoreScroll(cached.scrollY);
    // Keep the rendered page stable while a quiet refresh makes the next
    // visit current. Replacing it a second time would make scrolling jump.
    try {
      var refreshed = await fetchPage(url, request.signal);
      if (refreshed) saveCachedPage(url, refreshed);
    } catch (error) {
      // The cached page is still a useful, private fallback while offline.
    }
    return;
  }

  showNavigationSkeleton();
  restoreScroll(0);
  try {
    var page = await fetchPage(url, request.signal);
    if (!page) return;
    if (navigationRequest !== request) return;
    saveCachedPage(url, page);
    applyPage(page);
    clearNavigationSkeleton();
    restoreScroll(0);
  } catch (error) {
    if (error.name !== "AbortError") window.location.assign(url.href);
  }
}

function invalidateCachedTabs(tabNames) {
  var scope = navigationScope();
  if (!scope) return;
  var paths = {
    feed: "/feed/",
    saved: "/feed/read-later/",
    account: "/account/",
  };
  var prefix = "medpally:navigation:" + NAVIGATION_CACHE_VERSION + ":" + scope + ":";
  try {
    for (var index = window.sessionStorage.length - 1; index >= 0; index -= 1) {
      var key = window.sessionStorage.key(index);
      if (!key || key.indexOf(prefix) !== 0) continue;
      tabNames.forEach(function (tabName) {
        if (paths[tabName] && key.indexOf(prefix + paths[tabName]) === 0) {
          window.sessionStorage.removeItem(key);
        }
      });
    }
  } catch (error) {
    // Cache invalidation is an optimisation; the server remains authoritative.
  }
}

function prefetchFrequentlyUsedTabs() {
  var links = document.querySelectorAll('[data-tab-nav][href="/feed/"], [data-tab-nav][href="/feed/read-later/"]');
  var urls = [];
  links.forEach(function (link) {
    var url = new URL(link.href, window.location.href);
    if (url.href !== window.location.href && !readCachedPage(url) &&
      !urls.some(function (candidate) { return candidate.href === url.href; })) {
      urls.push(url);
    }
  });
  urls.reduce(function (promise, url) {
    return promise.then(async function () {
      try {
        var page = await fetchPage(url);
        if (page) saveCachedPage(url, page);
      } catch (error) {
        // Prefetching must never make the current page feel slower.
      }
    });
  }, Promise.resolve());
}

function initTabNavigation() {
  if (!navigationScope() || window.medpallyTabNavigationInitialised) return;
  window.medpallyTabNavigationInitialised = true;
  cacheCurrentPage();

  document.addEventListener("click", function (event) {
    var link = event.target.closest("a[data-tab-nav]");
    if (!link || isModifiedNavigation(event) || link.target) return;
    var url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin) return;
    if (link.closest(".drawer")) closeDrawer();
    event.preventDefault();
    navigateTo(url, { push: true });
  });
  window.addEventListener("popstate", function () {
    navigateTo(new URL(window.location.href), { fromHistory: true, push: false });
  });
  window.addEventListener("pagehide", cacheCurrentPage);
  document.addEventListener("htmx:afterRequest", function (event) {
    var form = event.detail.elt && event.detail.elt.closest("form[data-cache-invalidate]");
    if (event.detail.successful && form) {
      invalidateCachedTabs(form.dataset.cacheInvalidate.split(" "));
    }
  });

  var idle = window.requestIdleCallback || function (callback) { window.setTimeout(callback, 700); };
  idle(prefetchFrequentlyUsedTabs, { timeout: 1500 });
}

// Week groups.  A week's cards are flat siblings tagged with data-week rather
// than nested in a container: a week that straddles a page boundary arrives in
// two separate swaps, and the second half could never be nested back inside a
// container the first half rendered.  Selecting by attribute lets the whole
// week collapse as one thing however it arrived.
//
// Which weeks are open is a view preference rather than data, so it lives on
// the device and never reaches the server.  It records only the weeks the
// reader has actually changed, so the default (the newest weeks open) keeps
// applying as the weeks roll forward rather than pinning one week open for good.
var WEEK_STATE_VERSION = "v1";

function safeLocalGet(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (error) {
    return null;
  }
}

function safeLocalSet(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch (error) {
    // Private browsing and a full storage area should not break the toggle.
  }
}

function weekStateKey() {
  var scope = navigationScope();
  return scope ? "medpally:weeks:" + WEEK_STATE_VERSION + ":" + scope : "";
}

function readWeekState() {
  var key = weekStateKey();
  var raw = key && safeLocalGet(key);
  if (!raw) return {};
  try {
    var parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (error) {
    return {};
  }
}

function setWeekOpen(week, open) {
  document.querySelectorAll('.card[data-week="' + week + '"]').forEach(function (card) {
    card.hidden = !open;
  });
  document.querySelectorAll('[data-week-toggle="' + week + '"]').forEach(function (header) {
    header.setAttribute("aria-expanded", open ? "true" : "false");
  });
}

// Cards arrive from the server closed or open by its own default, because the
// server has no idea what this reader has since opened.  Reconciling on swap is
// what stops a week the reader opened from snapping shut when its second page
// lands.
function applyWeekState() {
  var headers = document.querySelectorAll("[data-week-toggle]");
  if (!headers.length) return;
  var state = readWeekState();
  headers.forEach(function (header) {
    var week = header.getAttribute("data-week-toggle");
    if (state[week] !== undefined) setWeekOpen(week, state[week] === true);
  });
}

function initWeekGroups() {
  if (window.medpallyWeekGroupsInitialised) return;
  window.medpallyWeekGroupsInitialised = true;
  document.addEventListener("click", function (event) {
    var header = event.target.closest("[data-week-toggle]");
    if (!header) return;
    var week = header.getAttribute("data-week-toggle");
    var open = header.getAttribute("aria-expanded") !== "true";
    setWeekOpen(week, open);
    var state = readWeekState();
    state[week] = open;
    safeLocalSet(weekStateKey(), JSON.stringify(state));
  });
  document.addEventListener("htmx:afterSwap", applyWeekState);
}

// Authentication is a full-page POST, so provide immediate feedback and block
// accidental double-submits while the browser follows the response or redirect.
function initAuthSubmitLoading() {
  document.querySelectorAll(".auth-shell form").forEach(function (form) {
    form.addEventListener("submit", function () {
      var button = form.querySelector('button[type="submit"]');
      if (!button || button.disabled) return;

      var label = button.textContent.trim().toLowerCase();
      var loadingLabel = label.indexOf("sign up") >= 0 ? "Creating account…" :
        label.indexOf("sign") >= 0 || label.indexOf("log") >= 0 ? "Signing in…" :
        label.indexOf("continue") >= 0 ? "Continuing…" : "Please wait…";
      button.disabled = true;
      button.classList.add("is-loading");
      button.setAttribute("aria-busy", "true");
      button.replaceChildren();
      var spinner = document.createElement("span");
      spinner.className = "button-spinner";
      spinner.setAttribute("aria-hidden", "true");
      button.appendChild(spinner);
      button.appendChild(document.createTextNode(loadingLabel));
    });
  });
}

// The service worker is what makes MedPally installable, and it keeps an
// offline notice to hand. Registration is best-effort: nothing on the page
// waits on it, and a browser without support simply carries on.
function initServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  window.addEventListener("load", function () {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
  });
}

document.addEventListener("DOMContentLoaded", function () {
  initDrawer();
  initBarTitle();
  initJournalGroupToggles();
  initFeedMenu();
  initWeekGroups();
  initFlashMessages();
  applyWeekState();
  initAuthSubmitLoading();
  initTabNavigation();
  initServiceWorker();
});
