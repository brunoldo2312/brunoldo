import os
import re
import time
import logging
import schedule
import tweepy
import google.generativeai as genai
from datetime import datetime
from dotenv import load_dotenv  # pip install python-dotenv

# ==========================================================
# 📂 CARREGAR VARIÁVEIS DE AMBIENTE
# ==========================================================
load_dotenv()

# ==========================================================
# 🔑 CHAVES DO X (do .env)
# ==========================================================
API_KEY             = os.getenv("X_API_KEY")
API_SECRET          = os.getenv("X_API_SECRET")
ACCESS_TOKEN        = os.getenv("X_ACCESS_TOKEN")
ACCESS_TOKEN_SECRET = os.getenv("X_ACCESS_TOKEN_SECRET")

# ==========================================================
# 🤖 CHAVE DO GEMINI (do .env)
# ==========================================================
GEMINI_KEY = os.getenv("GEMINI_KEY")

# ==========================================================
# 🌐 CONFIGURAÇÕES DO PROJETO
# ==========================================================
URL_MURAL = "https://brunoldo2312.github.io/brn-site/"
MAX_TWEET_LENGTH = 280
MAX_REPLY_LENGTH = 240

# Arquivo para persistir o ID da última menção respondida
ARQUIVO_ULTIMA_MENCAO = "ultima_mencao.txt"

# ==========================================================
# 📝 LOGS
# ==========================================================
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ==========================================================
# 🤖 INICIALIZAR GEMINI
# ==========================================================
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    model_gemini = genai.GenerativeModel("gemini-1.5-flash")
else:
    model_gemini = None

# ==========================================================
# 🧠 CONTEXTO DO PROJETO
# ==========================================================
CONTEXTO_BRN = """
Você é o assistente oficial do BRN — um mural de ordens P2P para negociação direta de criptomoedas na carteira, SEM intermediários.

INFORMAÇÕES OFICIAIS:
- Site: https://brunoldo2312.github.io/brn-site/
- Rede principal: Polygon
- Tokens: BRN, USDC, USDT, SHIB, WPOL, WBTC, WETH
- Carteiras aceitas: MetaMask, Rabby, Trust Wallet, Coinbase Wallet, OKX
- Não-custodial: você mantém suas chaves
- Escrow por contrato inteligente — troca atômica garantida

REGRAS:
- Seja direto e amigável
- Respeite o limite de caracteres
- Nunca peça chaves privadas ou seeds
- Nunca prometa lucros
- Responda em português
"""

# ==========================================================
# 🔐 AUTENTICAÇÃO NO X
# ==========================================================
MEU_USERNAME = None
MEU_USER_ID = None
client = None


def inicializar_x() -> bool:
    """Inicializa conexão com a API v2 do X."""
    global MEU_USERNAME, MEU_USER_ID, client

    try:
        if not all([API_KEY, API_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET]):
            logger.error("❌ Chaves da API do X não configuradas no .env!")
            return False

        client = tweepy.Client(
            consumer_key=API_KEY,
            consumer_secret=API_SECRET,
            access_token=ACCESS_TOKEN,
            access_token_secret=ACCESS_TOKEN_SECRET,
        )

        me = client.get_me()
        MEU_USERNAME = me.data.username
        MEU_USER_ID = me.data.id
        logger.info(f"✅ Autenticado como @{MEU_USERNAME} (ID: {MEU_USER_ID})")
        return True

    except Exception as e:
        logger.error(f"❌ Erro na autenticação do X: {e}")
        return False

# ==========================================================
# 📦 ESTADO GLOBAL
# ==========================================================
ordens_vistas = set()
ultima_mencao_id = None
contador_post = 0

# ==========================================================
# 💾 PERSISTÊNCIA: ÚLTIMA MENÇÃO
# ==========================================================
def salvar_ultima_mencao(tweet_id):
    try:
        with open(ARQUIVO_ULTIMA_MENCAO, "w") as f:
            f.write(str(tweet_id))
    except Exception as e:
        logger.warning(f"⚠️ Não foi possível salvar última menção: {e}")


def carregar_ultima_mencao():
    try:
        with open(ARQUIVO_ULTIMA_MENCAO, "r") as f:
            return int(f.read().strip())
    except Exception:
        return None

# ==========================================================
# 📝 FUNÇÃO: POSTAR TEXTO
# ==========================================================
def postar(texto: str) -> bool:
    """Posta um tweet. Retorna True se der certo."""
    if not client:
        logger.error("❌ Cliente do X não inicializado")
        return False

    try:
        if len(texto) > MAX_TWEET_LENGTH:
            texto = texto[:MAX_TWEET_LENGTH - 3] + "..."

        response = client.create_tweet(text=texto)
        tweet_id = response.data["id"]
        logger.info(f"✅ Postado (ID: {tweet_id}): {texto[:60]}...")
        return True

    except tweepy.TweepyException as e:
        if "429" in str(e):
            logger.warning("⚠️ Limite de taxa atingido! Esperando 60s...")
            time.sleep(60)
        logger.error(f"❌ Erro ao postar: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Erro inesperado ao postar: {e}")
        return False

# ==========================================================
# 🧠 FUNÇÃO: GERAR POST COM GEMINI
# ==========================================================
def gerar_post(tema: str) -> str:
    """Gera um tweet usando IA."""
    if not model_gemini:
        return f"📢 BRN — Negocie cripto P2P direto na carteira! Sem intermediários, seguro com escrow on-chain.\n🔗 {URL_MURAL}"

    prompt = (
        f"{CONTEXTO_BRN}\n\n"
        f"Gere um tweet curto e atrativo (máximo 240 caracteres) sobre: {tema}\n"
        f"- Use 1 ou 2 emojis\n"
        f"- Tom: profissional e confiável\n"
        f"- Termine com: {URL_MURAL}\n"
        f"Apenas o tweet, sem explicações."
    )

    try:
        response = model_gemini.generate_content(prompt)
        texto = response.text.strip().strip('"').strip("'")

        if URL_MURAL not in texto:
            texto = f"{texto}\n{URL_MURAL}"

        if len(texto) > MAX_TWEET_LENGTH:
            texto = texto[:MAX_TWEET_LENGTH - 3 - len(URL_MURAL)] + "...\n" + URL_MURAL

        return texto

    except Exception as e:
        logger.error(f"❌ Erro Gemini: {e}")
        return f"📢 BRN — Negocie cripto P2P direto na carteira! Sem intermediários, seguro com escrow on-chain.\n🔗 {URL_MURAL}"

# ==========================================================
# ⏰ FUNÇÃO: POST DIÁRIO
# ==========================================================
TEMAS = [
    "Como negociar cripto direto na carteira sem intermediários",
    "Por que o escrow do BRN é mais seguro que exchanges centralizadas",
    "Dicas para iniciantes em negociação P2P de cripto",
    "Vantagens de usar Polygon: taxas baixas e transações rápidas",
    "Como funciona o escrow inteligente do BRN — troca atômica garantida",
    "BRN: sua chave, seus tokens — negocie P2P com total soberania",
]


def post_diario():
    """Posta um tema rotativo."""
    global contador_post
    tema = TEMAS[contador_post % len(TEMAS)]
    contador_post += 1

    logger.info(f"📝 Gerando post sobre: {tema[:40]}...")
    texto = gerar_post(tema)
    postar(texto)

# ==========================================================
# 🔍 FUNÇÃO: VERIFICAR MURAL (SELETORES CORRETOS)
# ==========================================================
def verificar_mural():
    """Verifica o mural BRN e posta novas ordens."""
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # Bloqueia recursos desnecessários (mais rápido)
            page.route(
                "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ico}",
                lambda r: r.abort()
            )
            page.route("**/*analytics*", lambda r: r.abort())
            page.route("**/*tracker*", lambda r: r.abort())

            logger.info(f"🌐 Acessando mural: {URL_MURAL}")
            page.goto(URL_MURAL, wait_until="domcontentloaded", timeout=30000)

            # Espera o JS renderizar (seletor correto do app.js)
            try:
                page.wait_for_selector(
                    "#orders > .order, #orders > .empty",
                    timeout=20000
                )
            except Exception:
                logger.warning("⚠️ Timeout esperando ordens renderizarem.")
                browser.close()
                return

            ordens = page.query_selector_all("#orders > .order")

            if not ordens:
                logger.info("ℹ️ Nenhuma ordem encontrada no mural.")
                browser.close()
                return

            logger.info(f"📋 {len(ordens)} ordem(ns) encontrada(s).")

            for ordem in ordens:
                num_el = ordem.query_selector(".order-num")
                tag_el = ordem.query_selector(".tag")
                swap_sides = ordem.query_selector_all(".swap-side")

                num = num_el.inner_text().strip() if num_el else "#?"
                status = tag_el.inner_text().strip() if tag_el else ""

                oferece = ""
                pede = ""
                if len(swap_sides) >= 2:
                    oferece = swap_sides[0].inner_text().strip().replace("\n", " ")
                    pede = swap_sides[1].inner_text().strip().replace("\n", " ")

                chave = f"{num}|{oferece}|{pede}"
                if chave in ordens_vistas:
                    continue
                ordens_vistas.add(chave)

                # Só posta ordens ativas
                if "Executada" in status or "Cancelada" in status:
                    continue

                texto = (
                    f"🆕 Nova ordem ativa no mural BRN!\n\n"
                    f"{num} — {status}\n"
                    f"🔹 Oferece: {oferece}\n"
                    f"🔸 Pede: {pede}\n\n"
                    f"🔗 Confira: {URL_MURAL}"
                )
                postar(texto)
                time.sleep(5)  # respeita rate limit

            browser.close()

    except Exception as e:
        logger.error(f"❌ Erro ao verificar mural: {e}")

# ==========================================================
# 💬 FUNÇÃO: RESPONDER MENÇÕES
# ==========================================================
def responder_mencao(tweet_id: str, texto_pergunta: str, autor: str):
    """Gera e publica uma resposta a uma menção usando API v2."""

    if not texto_pergunta.strip():
        return

    if not model_gemini:
        logger.warning("⚠️ Gemini indisponível, pulando resposta.")
        return

    prompt = (
        f"{CONTEXTO_BRN}\n\n"
        f"PERGUNTA: {texto_pergunta}\n"
        f"RESPOSTA (máximo {MAX_REPLY_LENGTH} caracteres, amigável e clara):"
    )

    try:
        response = model_gemini.generate_content(prompt)
        resposta = response.text.strip()

        if len(resposta) > MAX_REPLY_LENGTH:
            resposta = resposta[:MAX_REPLY_LENGTH - 3] + "..."

        resposta_final = f"@{autor} {resposta}"

        client.create_tweet(text=resposta_final, in_reply_to_tweet_id=tweet_id)
        logger.info(f"💬 Respondido @{autor}: {texto_pergunta[:45]}...")

    except Exception as e:
        logger.error(f"❌ Erro ao responder: {e}")


def verificar_mencoes():
    """Verifica menções usando API v2."""
    global ultima_mencao_id

    if not client:
        return

    try:
        kwargs = {"max_results": 20}
        if ultima_mencao_id:
            kwargs["since_id"] = ultima_mencao_id

        resposta = client.get_users_mentions(
            id=MEU_USER_ID,
            **kwargs,
            tweet_fields=["text", "created_at", "author_id"],
            expansions=["author_id"],
            user_fields=["username"]
        )

        if not resposta.data:
            return

        usuarios = {u.id: u.username for u in resposta.includes.get("users", [])}

        for tweet in reversed(resposta.data):
            autor_username = usuarios.get(tweet.author_id, "desconhecido")

            # Remove menção ao bot
            texto_limpo = " ".join(
                w for w in tweet.text.split()
                if w.lower() != f"@{MEU_USERNAME}".lower()
            ).strip()

            logger.info(f"📩 Menção de @{autor_username}: {texto_limpo[:50]}...")
            responder_mencao(tweet.id, texto_limpo, autor_username)

            ultima_mencao_id = tweet.id
            salvar_ultima_mencao(tweet.id)
            time.sleep(3)

    except Exception as e:
        logger.error(f"❌ Erro ao verificar menções: {e}")

# ==========================================================
# 📅 AGENDAMENTOS
# ==========================================================
def agendar_tudo():
    schedule.clear()

    # 4 posts diários
    schedule.every().day.at("09:00").do(post_diario)
    schedule.every().day.at("12:00").do(post_diario)
    schedule.every().day.at("15:00").do(post_diario)
    schedule.every().day.at("18:00").do(post_diario)

    # Verificações periódicas
    schedule.every(45).minutes.do(verificar_mural)
    schedule.every(8).minutes.do(verificar_mencoes)

    logger.info("✅ Agendamentos configurados:")
    logger.info("   • 4 posts/dia (9h, 12h, 15h, 18h)")
    logger.info("   • Verificar mural: a cada 45 min")
    logger.info("   • Verificar menções: a cada 8 min")

# ==========================================================
# 🚀 MAIN
# ==========================================================
def main():
    global ultima_mencao_id

    logger.info("🤖 Bot BRN (X) iniciando...")

    # Validações
    if not GEMINI_KEY:
        logger.critical("⛔ GEMINI_KEY não configurada no .env")
        return

    if not inicializar_x():
        logger.critical("⛔ Falha na inicialização. Encerrando.")
        return

    # Carrega última menção vista
    ultima_mencao_id = carregar_ultima_mencao()
    if ultima_mencao_id:
        logger.info(f"💾 Retomando do ID de menção: {ultima_mencao_id}")

    agendar_tudo()

    # Loop principal
    while True:
        try:
            schedule.run_pending()
            time.sleep(15)
        except KeyboardInterrupt:
            logger.info("👋 Bot encerrado pelo usuário.")
            break
        except Exception as e:
            logger.error(f"❌ Erro no loop: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()