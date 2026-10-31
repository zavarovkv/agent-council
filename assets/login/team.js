(() => {
  "use strict";
  const main = document.querySelector("main");
  const prefix = main?.dataset.prefix || "";
  const cards = new Map([...document.querySelectorAll("[data-agent]")].map(card => [card.dataset.agent, card]));
  const t = window.CouncilText;
  let latest = null;
  let unavailable = false;
  const status = document.querySelector("[data-team-status]");
  for (const image of document.querySelectorAll("img[data-avatar]")) {
    image.src = `${prefix}/auth/login/council/avatars/${encodeURIComponent(image.dataset.avatar)}`;
  }
  function count(value) {
    return Number.isSafeInteger(value) && value >= 0 ? new Intl.NumberFormat(document.documentElement.lang).format(value) : "—";
  }
  async function update() {
    try {
      const response = await fetch(`${prefix}/auth/login/council/team`, {
        credentials: "omit", cache: "no-store", signal: AbortSignal.timeout(10000),
      });
      if (!response.ok) throw new Error("unavailable");
      const value = await response.json();
      if (value.schema !== 1 || !Array.isArray(value.agents) || value.agents.length !== cards.size) throw new Error("invalid");
      latest = value;
      unavailable = false;
      render();
    } catch {
      // Keep the previous rendered values, but make their freshness uncertainty visible.
      unavailable = true;
      render();
    }
  }
  function render() {
    if (latest) {
      for (const agent of latest.agents) {
        const card = cards.get(agent.slug);
        if (!card) throw new Error("unknown agent");
        const field = name => card.querySelector(`[data-field="${name}"]`);
        field("model").textContent = agent.model ? `${agent.model.name} · ${agent.model.effort}` : t("Не настроена", "Not configured");
        field("closed_tasks").textContent = count(agent.closed_tasks);
        field("tokens").textContent = count(agent.tokens);
        field("connection").textContent = agent.linear_connected === true
          ? t("Подключён к Linear", "Connected to Linear") : agent.linear_connected === false
            ? t("Подключение к Linear не завершено", "Linear connection incomplete") : t("Статистика Linear временно недоступна", "Linear statistics temporarily unavailable");
      }
      status.textContent = "";
    }
    if (unavailable) status.textContent = t("Не удалось обновить статистику. Показаны последние загруженные значения.", "Statistics could not be refreshed. Showing the last loaded values.");
  }
  document.addEventListener("council-language", render);
  update();
  setInterval(() => { if (!document.hidden) update(); }, 300000);
})();
