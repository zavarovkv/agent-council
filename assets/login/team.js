(() => {
  "use strict";
  const main = document.querySelector("main");
  const prefix = main?.dataset.prefix || "";
  const cards = new Map([...document.querySelectorAll("[data-agent]")].map(card => [card.dataset.agent, card]));
  const number = new Intl.NumberFormat("ru-RU");
  const status = document.querySelector("[data-team-status]");
  for (const image of document.querySelectorAll("img[data-avatar]")) {
    image.src = `${prefix}/auth/login/council/avatars/${encodeURIComponent(image.dataset.avatar)}`;
  }
  function count(value) {
    return Number.isSafeInteger(value) && value >= 0 ? number.format(value) : "—";
  }
  async function update() {
    try {
      const response = await fetch(`${prefix}/auth/login/council/team`, {
        credentials: "omit", cache: "no-store", signal: AbortSignal.timeout(10000),
      });
      if (!response.ok) throw new Error("unavailable");
      const value = await response.json();
      if (value.schema !== 1 || !Array.isArray(value.agents) || value.agents.length !== cards.size) throw new Error("invalid");
      for (const agent of value.agents) {
        const card = cards.get(agent.slug);
        if (!card) throw new Error("unknown agent");
        const field = name => card.querySelector(`[data-field="${name}"]`);
        field("model").textContent = agent.model ? `${agent.model.name} · ${agent.model.effort}` : "Не настроена";
        field("closed_tasks").textContent = count(agent.closed_tasks);
        field("tokens").textContent = count(agent.tokens);
        field("connection").textContent = agent.linear_connected === true
          ? "Подключён к Linear" : agent.linear_connected === false
            ? "Подключение к Linear не завершено" : "Статистика Linear временно недоступна";
      }
      status.textContent = "";
    } catch {
      // Keep the previous rendered values, but make their freshness uncertainty visible.
      status.textContent = "Не удалось обновить статистику. Показаны последние загруженные значения.";
    }
  }
  update();
  setInterval(() => { if (!document.hidden) update(); }, 300000);
})();
