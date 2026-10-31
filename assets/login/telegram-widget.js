/* Official Telegram Login owns the button and popup. Tokens never persist in browser storage. */
(function () {
  "use strict";
  const t = window.CouncilText;
  async function initialize() {
    const button = document.querySelector("button.tg-auth-button");
    const widget = document.querySelector(".telegram-widget");
    const fallback = document.querySelector("header .provider-list");
    const status = document.querySelector(".login-status");
    const link = Array.from(fallback?.querySelectorAll("a") || []).find((element) => {
      const url = new URL(element.href);
      return url.origin === location.origin && url.searchParams.get("provider") === "telegram-owner";
    });
    if (!button || !widget || !link) return;
    const mobile = matchMedia("(max-width: 600px)");
    const style = () => { button.dataset.style = mobile.matches ? "icon" : ""; };
    style();
    mobile.addEventListener("change", style);
    function message(text) {
      status.textContent = text;
      status.hidden = !text;
    }
    if (!window.Telegram?.Login) {
      message(t("Не удалось загрузить кнопку Telegram. Можно воспользоваться обычным входом в шапке.", "The Telegram button could not load. Use the sign-in link in the header."));
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
      if (!response.ok) throw new Error(result.detail || t("Не удалось выполнить вход. Попробуйте еще раз.", "Sign-in failed. Please try again."));
      return result;
    }
    async function prepare() {
      button.disabled = true;
      const config = await post("/challenge", { next: target.searchParams.get("next") || "/" });
      if (!/^[0-9]+$/.test(String(config.client_id)) || typeof config.nonce !== "string") {
        throw new Error(t("Не удалось подготовить вход через Telegram.", "Could not prepare Telegram sign-in."));
      }
      Telegram.Login.init({ client_id: config.client_id, scope: ["profile"], lang: document.documentElement.lang, nonce: config.nonce }, async (data) => {
        if (data.error || typeof data.id_token !== "string") {
          message(data.error === "popup_closed" ? t("Вход отменен.", "Sign-in cancelled.") : t("Telegram не подтвердил вход.", "Telegram did not confirm sign-in."));
        } else {
          button.disabled = true;
          message(t("Подтверждаем вход…", "Confirming sign-in…"));
          try {
            const result = await post("/complete", { id_token: data.id_token });
            const next = new URL(result.next || "/", location.origin);
            if (next.origin !== location.origin) throw new Error(t("Неверный адрес перехода.", "Invalid redirect URL."));
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
      widget.hidden = false;
      button.disabled = false;
    }
    function useFallback(error) {
      widget.hidden = true;
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
