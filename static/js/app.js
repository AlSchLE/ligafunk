// Ligafunk — client-side utilities.
// All behaviour is wired through data-* attributes so the Content-Security-
// Policy can forbid inline scripts and inline event handlers.

(function () {
  "use strict";

  function copyToClipboard(text, btn) {
    const done = () => {
      const original = btn.textContent;
      btn.textContent = "✓ Copied!";
      setTimeout(() => { btn.textContent = original; }, 2000);
    };

    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
    } else {
      fallbackCopy(text, done);
    }
  }

  function fallbackCopy(text, done) {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand("copy");
      done();
    } catch (err) {
      // Nothing else we can do; leave the label unchanged.
    } finally {
      document.body.removeChild(area);
    }
  }

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") || "" : "";
  }

  function postWebhook(btn) {
    const endpoint = btn.getAttribute("data-webhook-endpoint");
    if (!endpoint) {
      return;
    }
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Posting…";

    fetch(endpoint, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRF-Token": csrfToken() },
    })
      .then((response) => {
        if (!response.ok) {
          throw new Error("HTTP " + response.status);
        }
        return response.json();
      })
      .then((data) => {
        const results = Array.isArray(data.results) ? data.results : [];
        const ok = results.length > 0 && results.every(
          (item) => typeof item.status === "number" && item.status >= 200 && item.status < 300
        );
        btn.textContent = ok ? "✓ Posted!" : "⚠ Not posted";
      })
      .catch(() => {
        btn.textContent = "✗ Failed";
      })
      .finally(() => {
        btn.disabled = false;
        setTimeout(() => { btn.textContent = original; }, 3000);
      });
  }

  const SPORT_BRANDS = {
    generic: { name: "Fantasy Sports", icon: "🏆", primary: "#1a472a", accent: "#c9a227", tagline: "Recap & Team Management Platform" },
    soccer: { name: "Soccer", icon: "⚽", primary: "#006400", accent: "#ffffff", tagline: "Fantasy Soccer League" },
    football: { name: "Football", icon: "🏈", primary: "#1a472a", accent: "#c9a227", tagline: "Fantasy Football League" },
    baseball: { name: "Baseball", icon: "⚾", primary: "#003087", accent: "#ff0000", tagline: "Fantasy Baseball League" },
    basketball: { name: "Basketball", icon: "🏀", primary: "#ff6600", accent: "#000000", tagline: "Fantasy Basketball League" },
    hockey: { name: "Hockey", icon: "🏒", primary: "#000080", accent: "#ff0000", tagline: "Fantasy Hockey League" },
  };

  const LEAGUE_MAP = {
    "1389332038909198336": "soccer",
    "1387730345675210752": "football",
  };

  function updateBranding() {
    const input = document.getElementById("league-id-input");
    const icon = document.getElementById("sport-icon");
    const name = document.getElementById("sport-name");
    const tagline = document.getElementById("sport-tagline");
    if (!input || !icon || !name || !tagline) {
      return;
    }
    const brand = SPORT_BRANDS[LEAGUE_MAP[input.value.trim()]] || SPORT_BRANDS.generic;
    icon.textContent = brand.icon;
    name.textContent = brand.name;
    tagline.textContent = brand.tagline;
    document.documentElement.style.setProperty("--primary", brand.primary);
    document.documentElement.style.setProperty("--accent", brand.accent);
  }

  function initRecapHints() {
    const form = document.getElementById("recap-generator-form");
    const styleSelect = document.getElementById("recap-style");
    const themeSelect = document.getElementById("recap-theme");
    const hint = document.getElementById("recap-hint");
    if (!form || !styleSelect || !themeSelect || !hint) {
      return;
    }

    let styles = {};
    let themes = {};
    try {
      styles = JSON.parse(form.getAttribute("data-recap-styles") || "{}");
    } catch (err) {
      styles = {};
    }
    try {
      themes = JSON.parse(form.getAttribute("data-recap-themes") || "{}");
    } catch (err) {
      themes = {};
    }

    const refreshHint = () => {
      const style = styles[styleSelect.value] || {};
      const theme = themes[themeSelect.value] || {};
      hint.textContent = (style.description || "") +
        (theme.description ? " · " + theme.description : "");
    };

    // Seasonal suggestion, only for themes that exist.
    const month = new Date().getMonth() + 1;
    if (month === 12 && themes.christmas) {
      themeSelect.value = "christmas";
    } else if (month === 10 && themes.halloween) {
      themeSelect.value = "halloween";
    }

    styleSelect.addEventListener("change", refreshHint);
    themeSelect.addEventListener("change", refreshHint);
    refreshHint();
  }

  function init() {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", initRecapHints);
    } else {
      initRecapHints();
    }
  }

  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) {
      return;
    }

    const copyButton = target.closest("[data-copy]");
    if (copyButton) {
      copyToClipboard(copyButton.getAttribute("data-copy") || "", copyButton);
      return;
    }

    const webhookButton = target.closest("[data-webhook-endpoint]");
    if (webhookButton) {
      postWebhook(webhookButton);
      return;
    }

    const action = target.closest("[data-action]");
    if (action && action.getAttribute("data-action") === "update-branding") {
      updateBranding();
      return;
    }

    const trigger = target.closest("[data-trigger-file]");
    if (trigger) {
      const inputId = trigger.getAttribute("data-trigger-file");
      const input = inputId ? document.getElementById(inputId) : null;
      if (input) {
        input.click();
      }
    }
  });

  document.addEventListener("change", (event) => {
    const target = event.target;
    if (target && target.matches && target.matches("[data-auto-submit]")) {
      if (target.form) {
        if (typeof target.form.requestSubmit === "function") {
          target.form.requestSubmit();
        } else {
          target.form.submit();
        }
      }
    }
  });

  init();
})();
