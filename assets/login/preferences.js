/* Beauty owns presentation; only explicit, non-sensitive preferences are stored. */
(() => {
  "use strict";
  const root = document.documentElement;
  const read = key => { try { return localStorage.getItem(key); } catch { return null; } };
  const save = (key, value) => { try { localStorage.setItem(key, value); } catch {} };
  const system = matchMedia("(prefers-color-scheme: dark)");
  let theme = read("theme");
  const savedLanguage = read("language");
  root.lang = savedLanguage === "en" ? "en" : "ru";
  function applyTheme() {
    root.dataset.theme = ["light", "dark"].includes(theme) ? theme : system.matches ? "dark" : "light";
    const button = document.querySelector("[data-theme-toggle]");
    if (button) {
      button.textContent = root.dataset.theme === "dark" ? "☀" : "☾";
      button.setAttribute("aria-label", window.CouncilText("Переключить тему", "Switch theme"));
    }
  }
  window.CouncilText = (ru, en) => root.lang === "en" ? en : ru;
  applyTheme();
  system.addEventListener("change", applyTheme);
  function translate() {
    document.querySelectorAll("[data-en]").forEach(element => {
      element.dataset.ru ??= element.textContent;
      element.textContent = window.CouncilText(element.dataset.ru, element.dataset.en);
    });
    document.querySelectorAll("[data-en-label]").forEach(element => {
      element.dataset.ruLabel ??= element.getAttribute("aria-label");
      element.setAttribute("aria-label", window.CouncilText(element.dataset.ruLabel, element.dataset.enLabel));
    });
    const language = document.querySelector("[data-language-toggle]");
    language.textContent = root.lang === "ru" ? "RU / EN" : "EN / RU";
    language.setAttribute("aria-label", root.lang === "ru" ? "Switch to English" : "Переключить на русский");
    applyTheme();
    document.dispatchEvent(new Event("council-language"));
  }
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelector("[data-theme-toggle]").addEventListener("click", () => {
      theme = root.dataset.theme === "dark" ? "light" : "dark";
      save("theme", theme);
      applyTheme();
    });
    document.querySelector("[data-language-toggle]").addEventListener("click", () => {
      root.lang = root.lang === "ru" ? "en" : "ru";
      save("language", root.lang);
      translate();
    });
    translate();
  }, { once: true });
})();
