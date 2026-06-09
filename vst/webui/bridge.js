/*
 * SILA WebView bridge — the ONLY change the existing UI needs to run inside the
 * plugin. It re-implements app.js's api()/GET/POST/PUT/DEL on top of JUCE's
 * native function bridge instead of fetch("/api"...). Load this BEFORE app.js
 * in the plugin build (index.html script order), and delete app.js's own api()
 * helper (or guard it) so this one wins.
 *
 * Standalone app:   browser ──fetch /api──► FastAPI ──► engine
 * Plugin:           WebView ──backendCall──► C++ editor ──► engine
 *
 * The C++ side registers `backendCall` (see PluginEditor.cpp::handleBackendCall)
 * and returns the same JSON shapes the REST routes returned, so everything
 * downstream in app.js (knobs, grid, song mode, aurora theme) is unchanged.
 */
(function () {
  // window.__JUCE__ is injected by WebBrowserComponent when native integration
  // is enabled; backendCall is the C++ function registered in the editor.
  const callBackend = window.__JUCE__
    ? window.__JUCE__.getNativeFunction("backendCall")
    : async () => ({}); // graceful no-op when opened outside the plugin

  async function api(method, path, body) {
    // Same signature/return contract as app.js's api().
    const res = await callBackend(method, path, body ?? null);
    if (res && res.__error) throw new Error(res.__error);
    return res;
  }

  // Expose the same helpers app.js defines, so app.js can drop its fetch-based
  // versions and use these (no token, no HTTP).
  window.api = api;
  window.GET = (p) => api("GET", p);
  window.POST = (p, b) => api("POST", p, b);
  window.PUT = (p, b) => api("PUT", p, b);
  window.DEL = (p) => api("DELETE", p);

  // Server-pushed events replace the 2 s status poll: the processor emits
  // playhead/song-slot updates which we forward to app.js's existing handlers.
  if (window.__JUCE__ && window.__JUCE__.addEventListener) {
    window.__JUCE__.addEventListener("transport", (e) => {
      // e = { ppq, bpm, playing, currentSongSlot } — feed app.js's tickUI/status.
      window.dispatchEvent(new CustomEvent("sila-transport", { detail: e }));
    });
  }
})();
