import os
import threading
import time
from datetime import datetime
from functools import wraps
from flask import Flask, jsonify, render_template_string, request, Response
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

NGROK_AUTHTOKEN = os.environ.get("NGROK_AUTHTOKEN", "")
WEB_HOST = os.environ.get("BRN_WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("BRN_WEB_PORT", "5000"))
USE_NGROK = os.environ.get("BRN_USE_NGROK", "0") == "1"

WEB_USER = os.environ.get("BRN_WEB_USER", "admin")
WEB_PASS = os.environ.get("BRN_WEB_PASS", "")
FAUCET_MAX_PER_IP = os.environ.get("BRN_FAUCET_MAX_PER_IP", "3")

app = Flask(__name__)
limiter = Limiter(get_remote_address, app=app,
                  default_limits=["300 per hour"],
                  storage_uri="memory://")

_blockchain_ref = None
_faucet_handler = None


def set_blockchain(bc):
    global _blockchain_ref
    _blockchain_ref = bc


def set_faucet_handler(fn):
    global _faucet_handler
    _faucet_handler = fn


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not WEB_PASS:
            return Response("Servidor mal configurado: BRN_WEB_PASS vazio.", 500)
        auth = request.authorization
        if not auth or auth.username != WEB_USER or auth.password != WEB_PASS:
            return Response("Acesso negado.", 401,
                            {"WWW-Authenticate": 'Basic realm="BRN RWA"'})
        return f(*args, **kwargs)
    return decorated


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<title>BRN RWA — Painel</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { color-scheme: dark; }
  body { font-family: -apple-system,"Segoe UI",Roboto,sans-serif;
         background:#0d1117; color:#e6edf3; margin:0; padding:24px; }
  h1 { margin:0 0 4px; font-size:22px; }
  h2 { font-size:15px; margin:24px 0 8px; color:#8b949e;
       text-transform:uppercase; letter-spacing:.5px; }
  .sub { color:#8b949e; font-size:13px; margin-bottom:20px; }
  .cards { display:flex; gap:12px; margin-bottom:20px; flex-wrap:wrap; }
  .card { background:#161b22; border:1px solid #30363d; border-radius:8px;
          padding:12px 16px; min-width:150px; }
  .card .label { font-size:11px; color:#8b949e; text-transform:uppercase; }
  .card .value { font-size:20px; font-weight:600; margin-top:4px; }
  table { width:100%; border-collapse:collapse; background:#161b22;
          border-radius:8px; overflow:hidden; margin-bottom:12px; }
  th,td { padding:10px 12px; text-align:left; font-size:13px;
          border-bottom:1px solid #21262d; }
  th { background:#1c2128; font-weight:600; color:#8b949e;
       text-transform:uppercase; font-size:11px; }
  tr:hover { background:#1c2128; }
  .addr { font-family:Consolas,monospace; font-size:12px; color:#58a6ff; }
  .amount { color:#3fb950; font-weight:600; }
  .badge { padding:2px 8px; border-radius:10px; font-size:11px; font-weight:600; text-transform: capitalize; }
  .badge.confirmada, .badge.approved { background:#1f6feb33; color:#58a6ff; }
  .badge.pendente, .badge.pending { background:#d2992233; color:#d29922; }
  .badge.revoked, .badge.rejected { background:#f8514933; color:#f85149; }
  .empty { text-align:center; color:#8b949e; padding:40px; }
  .form { background:#161b22; border:1px solid #30363d; border-radius:8px;
          padding:16px; margin-bottom:20px; display:flex; gap:8px; flex-wrap:wrap; }
  .form input, .form select { padding:8px 12px; background:#0d1117; color:#e6edf3;
                  border:1px solid #30363d; border-radius:6px; font-family:monospace; }
  .form button { padding:8px 16px; background:#238636; color:#fff; border:0;
                 border-radius:6px; cursor:pointer; font-weight:600; }
  .form button:hover { background:#2ea043; }
</style>
</head>
<body>
  <h1>🏦 BRN RWA — Plataforma de Ativos Tokenizados</h1>
  <div class="sub">Atualização a cada 3s · <span id="last-update">—</span></div>

  <div class="cards">
    <div class="card"><div class="label">Blocos</div><div class="value" id="stat-blocks">—</div></div>
    <div class="card"><div class="label">Ativos</div><div class="value" id="stat-assets">—</div></div>
    <div class="card"><div class="label">KYC aprovados</div><div class="value" id="stat-kyc">—</div></div>
    <div class="card"><div class="label">Transferências</div><div class="value" id="stat-total">—</div></div>
    <div class="card"><div class="label">Slashed</div><div class="value" id="stat-slashed">—</div></div>
    <div class="card"><div class="label">Finalizado</div><div class="value" id="stat-final">—</div></div>
  </div>

  <div class="form">
    <input id="faucet-addr" placeholder="Endereço brn1… para o faucet BRN" size="50">
    <button onclick="requestFaucet()">Pedir BRN</button>
    <span id="faucet-msg" style="font-size:12px; align-self:center;"></span>
  </div>

  <h2>Ativos registrados</h2>
  <table>
    <thead><tr><th>ID</th><th>Nome</th><th>Tipo</th><th>Emissor</th>
      <th>Supply</th><th>Max</th><th>Restrito</th></tr></thead>
    <tbody id="assets-rows"><tr><td colspan="7" class="empty">Carregando…</td></tr></tbody>
  </table>

  <h2>Transferências recentes</h2>
  <table>
    <thead><tr><th>Data</th><th>Tipo</th><th>Ativo</th><th>Origem</th>
      <th>Destino</th><th>Valor</th><th>Status</th></tr></thead>
    <tbody id="tx-rows"><tr><td colspan="7" class="empty">Carregando…</td></tr></tbody>
  </table>

  <h2>Compliance (KYC)</h2>
  <table>
    <thead><tr><th>Endereço</th><th>Status</th><th>Nível</th>
      <th>Jurisdição</th><th>Verificado por</th><th>Expira</th></tr></thead>
    <tbody id="kyc-rows"><tr><td colspan="6" class="empty">Carregando…</td></tr></tbody>
  </table>

<script>
function shortAddr(a){ return a && a.length>22 ? a.slice(0,10)+'…'+a.slice(-8) : (a||'—'); }
function escapeHtml(str) { return str ? String(str).replace(/[&<>"']/g, m => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'} [m])) : ''; }

async function requestFaucet() {
  const addr = document.getElementById('faucet-addr').value.trim();
  const msgEl = document.getElementById('faucet-msg');
  if(!addr) { msgEl.textContent = "Informe um endereço."; msgEl.style.color = "#f85149"; return; }
  try {
    msgEl.textContent = "Enviando..."; msgEl.style.color = "#8b949e";
    const r = await fetch('/api/faucet', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ address: addr })
    });
    const d = await r.json();
    if(r.ok) { msgEl.textContent = d.message || "Sucesso!"; msgEl.style.color = "#3fb950"; }
    else { msgEl.textContent = d.error || "Erro na requisição."; msgEl.style.color = "#f85149"; }
  } catch(e) { msgEl.textContent = "Erro de conexão."; msgEl.style.color = "#f85149"; }
}

async function load(){
  try {
    const r = await fetch('/api/summary');
    if (r.status === 401) { document.body.innerHTML='<p style="padding:40px">Login necessário.</p>'; return; }
    const d = await r.json();
    document.getElementById('stat-blocks').textContent = d.stats.blocks;
    document.getElementById('stat-assets').textContent = d.stats.assets;
    document.getElementById('stat-kyc').textContent    = d.stats.kyc_approved;
    document.getElementById('stat-total').textContent  = d.stats.transfers;
    document.getElementById('stat-slashed').textContent= d.stats.slashed;
    document.getElementById('stat-final').textContent  = '#' + d.stats.finalized;
    document.getElementById('last-update').textContent = 'atualizado ' + d.stats.now;

    document.getElementById('assets-rows').innerHTML = d.assets.length ? d.assets.map(a => `
      <tr>
        <td class="addr">${escapeHtml(a.asset_id)}</td>
        <td>${escapeHtml(a.name)}</td>
        <td>${escapeHtml(a.asset_type)}</td>
        <td class="addr" title="${escapeHtml(a.issuer)}">${shortAddr(a.issuer)}</td>
        <td class="amount">${a.total_supply}</td>
        <td>${a.max_supply || '—'}</td>
        <td>${a.transfer_restricted ? '🔒' : '🟢'}</td>
      </tr>`).join('') : '<tr><td colspan="7" class="empty">Nenhum ativo.</td></tr>';

    document.getElementById('tx-rows').innerHTML = d.transfers.length ? d.transfers.map(t => `
      <tr>
        <td>${t.datetime}</td>
        <td>${escapeHtml(t.type)}</td>
        <td class="addr">${escapeHtml(t.asset_id)}</td>
        <td class="addr" title="${escapeHtml(t.from)}">${shortAddr(t.from)}</td>
        <td class="addr" title="${escapeHtml(t.to)}">${shortAddr(t.to)}</td>
        <td class="amount">${t.amount}</td>
        <td><span class="badge ${t.status}">${t.status}</span></td>
      </tr>`).join('') : '<tr><td colspan="7" class="empty">Nenhuma transferência.</td></tr>';

    document.getElementById('kyc-rows').innerHTML = d.kyc.length ? d.kyc.map(k => `
      <tr>
        <td class="addr" title="${escapeHtml(k.address)}">${shortAddr(k.address)}</td>
        <td><span class="badge ${k.status}">${k.status}</span></td>
        <td>${k.level}</td>
        <td>${escapeHtml(k.jurisdiction) || '—'}</td>
        <td class="addr" title="${escapeHtml(k.verified_by)}">${shortAddr(k.verified_by)}</td>
        <td>${k.expires_at || '—'}</td>
      </tr>`).join('') : '<tr><td colspan="6" class="empty">Nenhum KYC.</td></tr>';
  } catch(e) { console.error("Erro ao carregar dados", e); }
}

setInterval(load, 3000);
load();
</script>
</body>
</html>
"""

@app.route('/')
@require_auth
def index():
    return render_template_string(DASHBOARD_HTML)

@app.route('/api/summary')
@require_auth
def summary():
    return jsonify({
        "stats": {
            "blocks": 1240, 
            "assets": 3, 
            "kyc_approved": 12, 
            "transfers": 89, 
            "slashed": 0, 
            "finalized": 1235, 
            "now": _iso(time.time())
        },
        "assets": [
            {
