<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Jarvis Cripto • Dashboard</title>
  <style>
    :root {
      --bg-1: #0b1020;
      --bg-2: #121a33;
      --card: rgba(255, 255, 255, 0.07);
      --card-border: rgba(255, 255, 255, 0.12);
      --text: #e5e7eb;
      --muted: #9ca3af;
      --green: #22c55e;
      --red: #ef4444;
      --blue: #3b82f6;
      --yellow: #eab308;
      --shadow: 0 20px 60px rgba(0, 0, 0, 0.35);
      --input-bg: rgba(15, 23, 42, 0.6);
      --input-border: rgba(148, 163, 184, 0.28);
      --list-border: rgba(148, 163, 184, 0.12);
    }

    body.light {
      --bg-1: #f4f7ff;
      --bg-2: #eaf0ff;
      --card: rgba(255, 255, 255, 0.86);
      --card-border: rgba(15, 23, 42, 0.12);
      --text: #0f172a;
      --muted: #475569;
      --shadow: 0 16px 35px rgba(2, 6, 23, 0.12);
      --input-bg: rgba(255, 255, 255, 0.9);
      --input-border: rgba(100, 116, 139, 0.3);
      --list-border: rgba(100, 116, 139, 0.15);
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }

    html, body {
      height: 100%;
      font-family: Inter, Segoe UI, Roboto, Arial, sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at 10% 10%, rgba(29, 78, 216, 0.35) 0%, transparent 35%),
        radial-gradient(circle at 90% 20%, rgba(124, 58, 237, 0.30) 0%, transparent 30%),
        linear-gradient(135deg, var(--bg-1), var(--bg-2));
      transition: background 0.25s ease, color 0.25s ease;
    }

    .page {
      max-width: 1180px;
      margin: 0 auto;
      padding: 28px 18px 40px;
    }

    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 14px;
      margin-bottom: 22px;
      flex-wrap: wrap;
    }

    .title-wrap h1 {
      font-size: 1.8rem;
      font-weight: 800;
      letter-spacing: 0.3px;
    }

    .title-wrap p {
      margin-top: 6px;
      color: var(--muted);
      font-size: 0.95rem;
    }

    .header-actions {
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }

    .chip {
      border: 1px solid var(--card-border);
      background: var(--card);
      backdrop-filter: blur(8px);
      padding: 8px 12px;
      border-radius: 999px;
      font-size: 0.85rem;
      color: var(--text);
    }

    .theme-toggle {
      border: 1px solid var(--card-border);
      background: var(--card);
      color: var(--text);
      padding: 8px 12px;
      border-radius: 999px;
      cursor: pointer;
      font-size: 0.85rem;
      font-weight: 700;
      transition: transform 0.15s ease, opacity 0.2s ease;
    }

    .theme-toggle:hover {
      transform: translateY(-1px);
      opacity: 0.95;
    }

    .grid {
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 16px;
    }

    .card {
      background: var(--card);
      border: 1px solid var(--card-border);
      backdrop-filter: blur(10px);
      border-radius: 16px;
      padding: 18px;
      box-shadow: var(--shadow);
      transition: background 0.25s ease, border-color 0.25s ease;
    }

    .metric {
      grid-column: span 4;
      min-height: 120px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }

    .metric .label {
      color: var(--muted);
      font-size: 0.85rem;
      margin-bottom: 10px;
    }

    .metric .value {
      font-size: 1.8rem;
      font-weight: 800;
      letter-spacing: 0.3px;
      line-height: 1.1;
    }

    .metric .hint {
      font-size: 0.82rem;
      color: var(--muted);
      opacity: 0.95;
    }

    .controls {
      grid-column: span 12;
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }

    .btn {
      border: none;
      border-radius: 12px;
      padding: 11px 16px;
      color: white;
      font-weight: 700;
      font-size: 0.92rem;
      cursor: pointer;
      transition: transform 0.15s ease, opacity 0.2s ease, box-shadow 0.2s ease;
    }

    .btn:hover {
      transform: translateY(-1px);
      opacity: 0.95;
    }

    .btn:active {
      transform: translateY(0);
    }

    .btn-start {
      background: linear-gradient(135deg, #16a34a, #22c55e);
      box-shadow: 0 10px 24px rgba(34, 197, 94, 0.28);
    }

    .btn-stop {
      background: linear-gradient(135deg, #dc2626, #ef4444);
      box-shadow: 0 10px 24px rgba(239, 68, 68, 0.28);
    }

    .btn-filter {
      background: linear-gradient(135deg, #2563eb, #3b82f6);
      box-shadow: 0 10px 24px rgba(59, 130, 246, 0.28);
    }

    .filters {
      grid-column: span 12;
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 10px;
      align-items: end;
    }

    .field {
      grid-column: span 4;
      display: flex;
      flex-direction: column;
      gap: 7px;
    }

    .field label {
      font-size: 0.84rem;
      color: var(--muted);
    }

    .field input {
      background: var(--input-bg);
      border: 1px solid var(--input-border);
      color: var(--text);
      border-radius: 10px;
      padding: 11px 12px;
      font-size: 0.95rem;
      outline: none;
      transition: border-color 0.2s ease, box-shadow 0.2s ease;
    }

    .field input:focus {
      border-color: #60a5fa;
      box-shadow: 0 0 0 3px rgba(96, 165, 250, 0.18);
    }

    .logs-card {
      grid-column: span 12;
      padding: 0;
      overflow: hidden;
    }

    .logs-header {
      padding: 16px 18px;
      border-bottom: 1px solid var(--card-border);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    .logs-header h3 {
      font-size: 1.03rem;
    }

    .logs-count {
      color: var(--muted);
      font-size: 0.84rem;
    }

    .logs {
      list-style: none;
      max-height: 420px;
      overflow: auto;
    }

    .logs li {
      padding: 13px 18px;
      border-bottom: 1px solid var(--list-border);
      display: grid;
      grid-template-columns: 190px 110px 100px 1fr;
      gap: 10px;
      font-size: 0.9rem;
      align-items: center;
    }

    .logs li:last-child {
      border-bottom: none;
    }

    .log-time {
      color: var(--muted);
      font-size: 0.82rem;
    }

    .badge {
      display: inline-block;
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 0.78rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.4px;
      border: 1px solid transparent;
      width: fit-content;
    }

    .badge-running {
      background: rgba(34, 197, 94, 0.18);
      color: #86efac;
      border-color: rgba(34, 197, 94, 0.35);
    }

    .badge-stopped {
      background: rgba(239, 68, 68, 0.18);
      color: #fca5a5;
      border-color: rgba(239, 68, 68, 0.35);
    }

    .badge-neutral {
      background: rgba(234, 179, 8, 0.14);
      color: #fde68a;
      border-color: rgba(234, 179, 8, 0.3);
    }

    .pnl-pos { color: #16a34a; font-weight: 700; }
    .pnl-neg { color: #dc2626; font-weight: 700; }
    .pnl-zero { color: var(--text); font-weight: 700; }

    .empty {
      padding: 26px 18px;
      color: var(--muted);
      text-align: center;
      font-size: 0.92rem;
    }

    @media (max-width: 960px) {
      .metric { grid-column: span 6; }
      .field { grid-column: span 6; }
      .logs li {
        grid-template-columns: 1fr;
        gap: 6px;
      }
    }

    @media (max-width: 640px) {
      .metric { grid-column: span 12; }
      .field { grid-column: span 12; }
      .title-wrap h1 { font-size: 1.45rem; }
    }
  </style>
</head>
<body>
  <main class="page">
    <header class="header">
      <div class="title-wrap">
        <h1>🚀 Jarvis Cripto Dashboard</h1>
        <p>Monitoramento em tempo real do bot com visual premium.</p>
      </div>

      <div class="header-actions">
        <button class="theme-toggle" id="themeToggle" onclick="toggleTheme()">🌙 Dark</button>
        <div class="chip" id="lastUpdate">Última atualização: --</div>
      </div>
    </header>

    <section class="grid">
      <article class="card metric">
        <div class="label">Saldo</div>
        <div class="value" id="balance">--</div>
        <div class="hint">Conta principal</div>
      </article>

      <article class="card metric">
        <div class="label">PNL</div>
        <div class="value" id="pnl">--</div>
        <div class="hint">Resultado acumulado</div>
      </article>

      <article class="card metric">
        <div class="label">Status do Bot</div>
        <div class="value"><span class="badge badge-neutral" id="statusBadge">--</span></div>
        <div class="hint" id="statusText">Sem status</div>
      </article>

      <article class="card controls">
        <button class="btn btn-start" onclick="startBot()">▶ Start Bot</button>
        <button class="btn btn-stop" onclick="stopBot()">■ Stop Bot</button>
      </article>

      <article class="card filters">
        <div class="field">
          <label for="startDate">De</label>
          <input type="date" id="startDate" />
        </div>

        <div class="field">
          <label for="endDate">Até</label>
          <input type="date" id="endDate" />
        </div>

        <div class="field">
          <label>&nbsp;</label>
          <button class="btn btn-filter" onclick="loadLogs()">Filtrar logs</button>
        </div>
      </article>

      <article class="card logs-card">
        <div class="logs-header">
          <h3>Logs de Trades</h3>
          <span class="logs-count" id="logsCount">0 registros</span>
        </div>
        <ul class="logs" id="logs"></ul>
      </article>
    </section>
  </main>

  <script>
    const API = "https://just-peace-production.up.railway.app";

    function formatUSD(value) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
      return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD"
      }).format(value);
    }

    function formatDateTime(value) {
      try {
        return new Date(value).toLocaleString("pt-BR");
      } catch {
        return "--";
      }
    }

    function updateLastUpdate() {
      const now = new Date().toLocaleTimeString("pt-BR");
      document.getElementById("lastUpdate").innerText = `Última atualização: ${now}`;
    }

    function applyTheme(theme) {
      document.body.classList.toggle("light", theme === "light");
      const btn = document.getElementById("themeToggle");
      btn.innerText = theme === "light" ? "☀️ Light" : "🌙 Dark";
      localStorage.setItem("theme", theme);
    }

    function toggleTheme() {
      const current = document.body.classList.contains("light") ? "light" : "dark";
      applyTheme(current === "light" ? "dark" : "light");
    }

    function updateStatus(status) {
      const statusBadge = document.getElementById("statusBadge");
      const statusText = document.getElementById("statusText");
      const normalized = (status || "").toString().toLowerCase();

      statusBadge.className = "badge";
      if (normalized.includes("run") || normalized.includes("ativo") || normalized.includes("on")) {
        statusBadge.classList.add("badge-running");
      } else if (normalized.includes("stop") || normalized.includes("off") || normalized.includes("parado")) {
        statusBadge.classList.add("badge-stopped");
      } else {
        statusBadge.classList.add("badge-neutral");
      }

      statusBadge.innerText = status || "--";
      statusText.innerText = status ? `Bot atualmente: ${status}` : "Sem status";
    }

    async function getPnlFallbackFromLogs() {
      try {
        const res = await fetch(`${API}/logs`);
        const logs = await res.json();
        if (!Array.isArray(logs)) return 0;
        return logs.reduce((sum, item) => sum + (Number(item.pnl) || 0), 0);
      } catch (error) {
        console.error("Erro ao calcular PNL fallback:", error);
        return 0;
      }
    }

    async function loadDashboard() {
      try {
        const res = await fetch(`${API}/dashboard`);
        const data = await res.json();

        document.getElementById("balance").innerText = formatUSD(data.balance);

        let pnlValue = Number(data.pnl);
        if (!Number.isFinite(pnlValue)) {
          pnlValue = await getPnlFallbackFromLogs();
        }

        const pnlElement = document.getElementById("pnl");
        pnlElement.innerText = formatUSD(pnlValue);

        pnlElement.classList.remove("pnl-pos", "pnl-neg", "pnl-zero");
        if (pnlValue > 0) pnlElement.classList.add("pnl-pos");
        else if (pnlValue < 0) pnlElement.classList.add("pnl-neg");
        else pnlElement.classList.add("pnl-zero");

        updateStatus(data.status);
      } catch (error) {
        console.error("Erro ao carregar dashboard:", error);
      } finally {
        updateLastUpdate();
      }
    }

    async function loadLogs() {
      try {
        const start = document.getElementById("startDate").value;
        const end = document.getElementById("endDate").value;

        let url = `${API}/logs`;
        const params = new URLSearchParams();
        if (start) params.append("start", start);
        if (end) params.append("end", end);
        if ([...params].length) url += `?${params.toString()}`;

        const res = await fetch(url);
        const logs = await res.json();

        const list = document.getElementById("logs");
        list.innerHTML = "";

        if (!Array.isArray(logs) || logs.length === 0) {
          list.innerHTML = `<li class="empty">Nenhum log encontrado para o período selecionado.</li>`;
          document.getElementById("logsCount").innerText = "0 registros";
          return;
        }

        logs.forEach((log) => {
          const li = document.createElement("li");

          const pnlValue = Number(log.pnl);
          const pnlClass = pnlValue > 0 ? "pnl-pos" : pnlValue < 0 ? "pnl-neg" : "pnl-zero";
          const action = (log.action || "--").toString().toUpperCase();

          li.innerHTML = `
            <span class="log-time">${formatDateTime(log.time)}</span>
            <span>${log.pair || "--"}</span>
            <span>${action}</span>
            <span class="${pnlClass}">${formatUSD(log.pnl)}</span>
          `;
          list.appendChild(li);
        });

        document.getElementById("logsCount").innerText = `${logs.length} registro(s)`;
      } catch (error) {
        console.error("Erro ao carregar logs:", error);
      }
    }

    async function startBot() {
      try {
        await fetch(`${API}/bot/start`, { method: "POST" });
      } catch (error) {
        console.error("Erro ao iniciar bot:", error);
      } finally {
        loadDashboard();
      }
    }

    async function stopBot() {
      try {
        await fetch(`${API}/bot/stop`, { method: "POST" });
      } catch (error) {
        console.error("Erro ao parar bot:", error);
      } finally {
        loadDashboard();
      }
    }

    const savedTheme = localStorage.getItem("theme") || "dark";
    applyTheme(savedTheme);

    setInterval(() => {
      loadDashboard();
      loadLogs();
    }, 3000);

    loadDashboard();
    loadLogs();
  </script>
</body>
</html>

