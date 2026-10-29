/* Official Telegram Login owns the button and popup. Tokens never persist in browser storage. */
(function () {
  "use strict";
  async function initialize() {
    const button = document.querySelector("button.tg-auth-button");
    const fallback = document.querySelector("header .provider-list");
    const status = document.querySelector(".login-status");
    const link = Array.from(fallback?.querySelectorAll("a") || []).find((element) => {
      const url = new URL(element.href);
      return url.origin === location.origin && url.searchParams.get("provider") === "telegram-owner";
    });
    if (!button || !link) return;
    function message(text) {
      status.textContent = text;
      status.hidden = !text;
    }
    if (!window.Telegram?.Login) {
      message("Не удалось загрузить кнопку Telegram. Можно воспользоваться обычным входом в шапке.");
      return;
    }
    const target = new URL(link.href);
    const endpoint = target.pathname + "/telegram-widget";
    async function post(path, body) {
      const response = await fetch(endpoint + path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(body),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || "Не удалось выполнить вход. Попробуйте еще раз.");
      return result;
    }
    async function prepare() {
      button.disabled = true;
      const config = await post("/challenge", { next: target.searchParams.get("next") || "/" });
      if (!/^[0-9]+$/.test(String(config.client_id)) || typeof config.nonce !== "string") {
        throw new Error("Не удалось подготовить вход через Telegram.");
      }
      Telegram.Login.init({ client_id: config.client_id, scope: ["profile"], lang: "ru", nonce: config.nonce }, async (data) => {
        if (data.error || typeof data.id_token !== "string") {
          message(data.error === "popup_closed" ? "Вход отменен." : "Telegram не подтвердил вход.");
        } else {
          button.disabled = true;
          message("Подтверждаем вход…");
          try {
            const result = await post("/complete", { id_token: data.id_token });
            const next = new URL(result.next || "/", location.origin);
            if (next.origin !== location.origin) throw new Error("Неверный адрес перехода.");
            Telegram.Login.close();
            location.assign(next.href);
            return;
          } catch (error) {
            Telegram.Login.close();
            message(error.message);
          }
        }
        try { await prepare(); } catch (error) { useFallback(error); }
      });
      fallback.hidden = true;
      button.hidden = false;
      button.disabled = false;
    }
    function useFallback(error) {
      button.hidden = true;
      fallback.hidden = false;
      message(error.message);
    }
    button.addEventListener("click", () => message(""));
    try { await prepare(); } catch (error) { useFallback(error); }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize, { once: true });
  } else {
    initialize();
  }
})();
