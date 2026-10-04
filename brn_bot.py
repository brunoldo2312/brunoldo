import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from datetime import time as dt_time

import google.generativeai as genai
import pytz
from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.constants import ChatType
from telegram.error import Forbidden, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ==========================================================
# 📂 CARREGAR .env
# ==========================================================
load_dotenv()

TOKEN            = os.getenv("TELEGRAM_TOKEN")
GEMINI_KEY       = os.getenv("GEMINI_KEY")
CHAT_ID          = os.getenv("CHAT_ID")
LINK_CONVITE     = os.getenv("LINK_CONVITE", "")
MODO_IA_GRUPO    = os.getenv("MODO_IA_GRUPO", "mencao").lower()
IA_NO_PRIVADO    = os.getenv("IA_NO_PRIVADO", "True").lower() == "true"
LIMITE_RESPOSTAS = int(os.getenv("LIMITE_RESPOSTAS_POR_MINUTO", "3"))

URL_MURAL = "https://brunoldo2312.github.io/brn-site/"
ARQUIVO_ESTADO = "estado_brn.json"

if not TOKEN:
    raise SystemExit("⛔ TELEGRAM_TOKEN não configurado no .env")
if not GEMINI_KEY:
    raise SystemExit("⛔ GEMINI_KEY não configurada no .env")
if not CHAT_ID:
    raise SystemExit("⛔ CHAT_ID não configurado no .env")

# ==========================================================
# 📝 LOGS
# ==========================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("BRN")

# ==========================================================
# 🤖 GEMINI (com detecção de chave inválida)
# ==========================================================
GEMINI_OK = False
model_gemini = None

try:
    genai.configure(api_key=GEMINI_KEY)
    model_gemini = genai.GenerativeModel("gemini-1.5-flash")
    GEMINI_OK = True
    logger.info("✅ Gemini configurado")
except Exception as e:
    logger.warning(f"⚠️ Gemini não configurado: {e}")
    logger.warning("   O bot vai funcionar SEM IA (só posts fixos + mural).")

# ==========================================================
# 📚 CONTEXTO DO PROJETO
# ==========================================================
CONTEXTO_BRN = """
Você é o assistente oficial do BRN — um mural de ordens P2P (peer-to-peer)
para negociação direta de criptomoedas na carteira, SEM intermediários.

INFORMAÇÕES OFICIAIS:
- Site: https://brunoldo2312.github.io/brn-site/
- Grupo Telegram: https://t.me/+cMGi3um07ZxlYWRh
- Rede principal: Polygon (também suporta Ethereum e BSC via bridge)
- Tokens: BRN, USDC, USDT, SHIB, WPOL, WBTC, WETH
- Carteiras: MetaMask, Rabby, Trust Wallet, Coinbase Wallet, OKX, Phantom, Brave
- Não-custodial: você mantém suas chaves
- Escrow por contrato inteligente — troca atômica garantida
- Bridge cross-chain via SideShift e Symbiosis

COMO FUNCIONA:
1. Conecte a carteira no site
2. Crie uma ordem (oferece X, pede Y)
3. Outro usuário aceita
4. O contrato de escrow executa a troca automaticamente

REGRAS DE RESPOSTA:
- Seja direto, amigável e profissional
- Use emojis com moderação
- NUNCA invente informações
- NUNCA peça chaves privadas ou seeds
- NUNCA prometa lucros
- Responda em português
- Máximo 3 parágrafos curtos
- Se não for sobre BRN/cripto/P2P: "Só posso ajudar com assuntos relacionados ao BRN e negociação P2P. 😊"
"""

# ==========================================================
# 📦 ESTADO GLOBAL + PERSISTÊNCIA
# ==========================================================
ordens_vistas: set = set()
contador_post: int = 0
_historico_respostas = defaultdict(list)


def _carregar_estado() -> None:
    global ordens_vistas, contador_post
    if not os.path.exists(ARQUIVO_ESTADO):
        logger.info("ℹ️ Nenhum estado anterior. Iniciando do zero.")
        return
    try:
        with open(ARQUIVO_ESTADO, "r", encoding="utf-8") as f:
            dados = json.load(f)
        ordens_vistas = set(dados.get("ordens_vistas", []))
        contador_post = dados.get("contador_post", 0)
        logger.info(
            f"💾 Estado restaurado: {len(ordens_vistas)} ordens | "
            f"post #{contador_post}"
        )
    except Exception as e:
        logger.warning(f"⚠️ Falha ao ler estado: {e}")


def _salvar_estado() -> None:
    try:
        dados = {
            "ordens_vistas": list(ordens_vistas),
            "contador_post": contador_post,
            "atualizado_em": time.time(),
        }
        tmp = ARQUIVO_ESTADO + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
        os.replace(tmp, ARQUIVO_ESTADO)
    except Exception as e:
        logger.warning(f"⚠️ Falha ao salvar estado: {e}")


# ==========================================================
# 🛡️ ENVIO COM RETRY + BACKOFF
# ==========================================================
async def enviar_seguro(bot, chat_id, texto, tentativas=3, **kwargs) -> bool:
    for tentativa in range(1, tentativas + 1):
        try:
            await bot.send_message(chat_id=chat_id, text=texto, **kwargs)
            return True
        except RetryAfter as e:
            espera = int(e.retry_after) + 1
            logger.warning(f"⚠️ Rate limit TG. Aguardando {espera}s")
            await asyncio.sleep(espera)
        except Forbidden as e:
            logger.error(f"🚫 Bot bloqueado do chat {chat_id}: {e}")
            return False
        except TelegramError as e:
            espera = 2 ** tentativa
            logger.warning(f"⚠️ Erro TG (tentativa {tentativa}): {e}")
            await asyncio.sleep(espera)
        except Exception as e:
            logger.error(f"❌ Erro inesperado: {e}")
            return False
    return False


# ==========================================================
# 🚦 ANTI-SPAM
# ==========================================================
def pode_responder(user_id: int) -> bool:
    agora = time.time()
    hist = _historico_respostas[user_id]
    hist[:] = [t for t in hist if agora - t < 60]
    if len(hist) >= LIMITE_RESPOSTAS:
        return False
    hist.append(agora)
    return True


# ==========================================================
# 💬 /start
# ==========================================================
TEXTO_START = (
    "🚀 *BRN — Mural de Ordens P2P*\n\n"
    "Negocie direto na carteira, sem intermediário.\n"
    "Site oficial:\n"
    f"{URL_MURAL}"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    btn = [[InlineKeyboardButton("🌐 Abrir Mural BRN", url=URL_MURAL)]]
    await update.message.reply_text(
        TEXTO_START,
        reply_markup=InlineKeyboardMarkup(btn),
        parse_mode="Markdown",
        disable_web_page_preview=False,
    )


# ==========================================================
# 📨 /convidar_contatos
# ==========================================================
MENSAGEM_CONVITE = (
    "🚀 *Olá!*\n\n"
    "Te convido a participar do grupo oficial *BRN crypto* — "
    "mural de ordens P2P onde você negocia direto na carteira, sem intermediário.\n\n"
    "👉 Entre aqui: {link}\n\n"
    "Nos vemos lá! 🔷"
)

CONTATOS: list = [
    # 123456789,
]


async def convidar_contatos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not CONTATOS:
        await update.message.reply_text(
            "⚠️ Nenhum contato configurado.\n"
            "Edite a lista `CONTATOS` no código."
        )
        return

    msg_status = await update.message.reply_text(
        f"⏳ Enviando convites para {len(CONTATOS)} contato(s)..."
    )

    enviados, falhas = 0, 0
    texto = MENSAGEM_CONVITE.format(link=LINK_CONVITE)

    for uid in CONTATOS:
        ok = await enviar_seguro(
            context.bot, chat_id=uid, texto=texto,
            parse_mode="Markdown", disable_web_page_preview=False,
        )
        if ok:
            enviados += 1
        else:
            falhas += 1
        await asyncio.sleep(1.2)

    await msg_status.edit_text(
        f"✅ Convites concluídos!\n\n"
        f"📤 Enviados: {enviados}\n"
        f"❌ Falhas: {falhas}",
    )


# ==========================================================
# 💬 IA — RESPOSTAS
# ==========================================================
PALAVRAS_CHAVE = [
    "brn", "mural", "ordem", "escrow", "p2p", "troca", "carteira",
    "polygon", "usdc", "usdt", "shib", "como funciona", "seguro",
    "metamask", "gas", "taxa", "bridge", "site", "wallet",
]


async def responder_mensagem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not GEMINI_OK:
        return

    msg: Message = update.message or update.channel_post
    if not msg or not msg.text:
        return

    texto = msg.text.strip()
    if texto.startswith("/"):
        return

    chat = msg.chat
    eh_grupo = chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

    deve = False
    pergunta = texto

    if eh_grupo:
        bot_username = context.bot.username or ""
        mencionado = bot_username and f"@{bot_username}" in texto

        if MODO_IA_GRUPO == "sempre":
            deve = True
        elif mencionado:
            pergunta = texto.replace(f"@{bot_username}", "").strip()
            deve = True
        else:
            tem_kw = any(p in texto.lower() for p in PALAVRAS_CHAVE)
            if tem_kw and len(texto) > 15:
                deve = True
    else:
        deve = IA_NO_PRIVADO

    if not deve:
        return

    user_id = msg.from_user.id if msg.from_user else None
    if user_id and not pode_responder(user_id):
        return

    try:
        prompt = (
            f"{CONTEXTO_BRN}\n\n"
            f"PERGUNTA:\n{pergunta}\n\n"
            f"RESPOSTA (máx 3 parágrafos curtos):"
        )
        response = await model_gemini.generate_content_async(prompt)
        resposta = (response.text or "").strip()

        if not resposta:
            resposta = f"Não consegui processar. Consulte: {URL_MURAL}"
        if len(resposta) > 3500:
            resposta = resposta[:3500] + "..."

        await msg.reply_text(resposta, disable_web_page_preview=True)
        logger.info(f"💬 Respondido {user_id}: {pergunta[:40]}...")

    except Exception as e:
        logger.error(f"Erro IA: {e}")


# ==========================================================
# 🧠 GERAR POST
# ==========================================================
TEMAS_FALLBACK = [
    "Negocie cripto P2P direto na carteira, sem intermediários, com escrow on-chain.",
    "Sua chave, seus tokens. Soberania total no BRN — mural de ordens P2P.",
    "Escrow inteligente: troca atômica garantida, sem confiar no outro.",
    "Taxas baixas na Polygon. Negocie BRN, USDC, USDT e mais.",
]


async def gerar_post_gemini(tema: str, indice: int = 0) -> str:
    fallback = f"📢 {TEMAS_FALLBACK[indice % len(TEMAS_FALLBACK)]}\n🔗 {URL_MURAL}"
    if not GEMINI_OK:
        return fallback

    prompt = (
        f"Você é o community manager do BRN — mural de ordens P2P.\n\n"
        f"Gere um post curto (máx. 500 caracteres) sobre: {tema}\n\n"
        f"Regras:\n- Use emojis\n- Termine com: {URL_MURAL}\n"
        f"- Texto simples, sem Markdown\n- Tom: profissional e confiável"
    )
    try:
        response = await model_gemini.generate_content_async(prompt)
        texto = (response.text or "").strip()
        if not texto:
            return fallback
        if URL_MURAL not in texto:
            texto = f"{texto}\n{URL_MURAL}"
        return texto
    except Exception as e:
        logger.error(f"Erro Gemini: {e}")
        return fallback


# ==========================================================
# ⏰ POST DIÁRIO
# ==========================================================
TEMAS = [
    "Como negociar BRN direto na carteira sem intermediários",
    "Segurança e transparência nas negociações P2P",
    "Por que o BRN é diferente de exchanges centralizadas",
    "Dicas para iniciantes no mural de ordens BRN",
    "Vantagens do escrow inteligente — troca atômica",
    "BRN: sua chave, seus tokens — soberania total",
]


async def post_diario(context: ContextTypes.DEFAULT_TYPE) -> None:
    global contador_post
    idx = contador_post
    tema = TEMAS[idx % len(TEMAS)]
    contador_post += 1
    _salvar_estado()

    texto = await gerar_post_gemini(tema, idx)
    ok = await enviar_seguro(context.bot, chat_id=CHAT_ID, texto=texto)
    if ok:
        logger.info(f"✅ Post enviado: {tema}")
    else:
        logger.error(f"❌ Falha post: {tema}")


# ==========================================================
# 🔍 VERIFICAR MURAL
# ==========================================================
async def verificar_mural(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("❌ Playwright não instalado.")
        return

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            try:
                page = await browser.new_page()
                await page.route(
                    "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ico,css}",
                    lambda r: r.abort(),
                )
                await page.goto(URL_MURAL, wait_until="domcontentloaded", timeout=30000)

                try:
                    await page.wait_for_selector(
                        "#orders > .order, #orders > .empty", timeout=20000
                    )
                except Exception:
                    logger.warning("⚠️ Timeout esperando ordens.")
                    return

                ordens = await page.query_selector_all("#orders > .order")
                if not ordens:
                    logger.info("ℹ️ Mural vazio.")
                    return

                logger.info(f"📋 {len(ordens)} ordem(ns).")

                novas = 0
                for ordem in ordens:
                    num_el = await ordem.query_selector(".order-num")
                    tag_el = await ordem.query_selector(".tag")
                    escrow_el = await ordem.query_selector(".order-id")
                    swap_sides = await ordem.query_selector_all(".swap-side")

                    num = (await num_el.inner_text()).strip() if num_el else "#?"
                    status = (await tag_el.inner_text()).strip() if tag_el else ""
                    escrow = (await escrow_el.inner_text()).strip() if escrow_el else ""

                    oferece, pede = "", ""
                    if len(swap_sides) >= 2:
                        oferece = (await swap_sides[0].inner_text()).strip().replace("\n", " ")
                        pede = (await swap_sides[1].inner_text()).strip().replace("\n", " ")

                    chave = f"{num}|{escrow}|{oferece}|{pede}"
                    if chave in ordens_vistas:
                        continue
                    ordens_vistas.add(chave)

                    if "Executada" in status or "Cancelada" in status:
                        continue

                    texto = (
                        f"🆕 *Nova ordem no mural BRN!*\n\n"
                        f"{num} — {status}\n"
                        f"🔹 *Oferece:* {oferece}\n"
                        f"🔸 *Pede:* {pede}\n\n"
                        f"🔗 {URL_MURAL}"
                    )

                    ok = await enviar_seguro(
                        context.bot, chat_id=CHAT_ID, texto=texto,
                        parse_mode="Markdown", disable_web_page_preview=True,
                    )
                    if ok:
                        logger.info(f"🆕 Ordem postada: {num}")
                        novas += 1
                        await asyncio.sleep(2)

                if novas:
                    _salvar_estado()

            finally:
                await browser.close()

    except Exception as e:
        logger.error(f"Erro mural: {e}")


# ==========================================================
# 📅 AGENDAMENTOS
# ==========================================================
def agendar_tudo(app: Application) -> None:
    tz = pytz.timezone("America/Sao_Paulo")
    horarios = [
        dt_time(hour=9,  minute=0, tzinfo=tz),
        dt_time(hour=12, minute=0, tzinfo=tz),
        dt_time(hour=15, minute=0, tzinfo=tz),
        dt_time(hour=18, minute=0, tzinfo=tz),
    ]
    for h in horarios:
        app.job_queue.run_daily(post_diario, time=h, name=f"post_{h.hour}")

    app.job_queue.run_repeating(
        verificar_mural, interval=300, first=10, name="monitor_mural"
    )
    logger.info("✅ 4 posts/dia + monitor 5 min agendados.")


# ==========================================================
# 🧹 SHUTDOWN
# ==========================================================
async def on_shutdown(app: Application) -> None:
    logger.info("🧹 Salvando estado...")
    _salvar_estado()


# ==========================================================
# 🚀 MAIN
# ==========================================================
def main() -> None:
    _carregar_estado()

    app = (
        Application.builder()
        .token(TOKEN)
        .post_shutdown(on_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("site", start))
    app.add_handler(CommandHandler("brn", start))
    app.add_handler(CommandHandler("convidar_contatos", convidar_contatos))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, responder_mensagem)
    )

    agendar_tudo(app)

    logger.info(f"🤖 Bot BRN rodando... IA={'ON' if GEMINI_OK else 'OFF'}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()