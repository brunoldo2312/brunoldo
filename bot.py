import os
import json
import time
import logging
import schedule
import tweepy
import google.generativeai as genai
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# ==========================================================
# 📂 CARREGAR VARIÁVEIS DE AMBIENTE
# ==========================================================
load_dotenv()

API_KEY             = os.getenv("X_API_KEY")
API_SECRET          = os.getenv("X_API_SECRET")
ACCESS_TOKEN        = os.getenv("X_ACCESS_TOKEN")
ACCESS_TOKEN_SECRET = os.getenv("X_ACCESS_TOKEN_SECRET")
GEMINI_KEY          = os.getenv("GEMINI_KEY")

# ==========================================================
# 🌐 CONFIGURAÇÕES
# ==========================================================
URL_MURAL = "https://brunoldo2312.github.io/brn-site/"
MAX_TWEET_LENGTH = 280
MAX_REPLY_LENGTH = 240
ARQUIVO_ESTADO = "estado_bot.json"

# ==========================================================
# 📝 LOGS
# ==========================================================
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ==========================================================
# 🤖 GEMINI
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
# 🔐 ESTADO GLOBAL
# ==========================================================
MEU_USERNAME = None
MEU_USER_ID = None
client = None
ordens_vistas = set()
ultima_mencao_id = None
contador_post = 0

# ==========================================================
# 🔑 AUTENTICAÇÃO NO X
# ==========================================================
def inicializar_x() -> bool:
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
# 💾 PERSISTÊNCIA DE ESTADO
# ==========================================================
def salvar_estado():
    try:
        dados = {
            "ultima_mencao_id": ultima_mencao_id,
            "ordens_vistas": list(ordens_vistas),
            "contador_post": contador_post,
        }
        with open(ARQUIVO_ESTADO, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"⚠️ Não foi possível salvar o estado: {e}")


def carregar_estado():
    global ultima_mencao_id, ordens_vistas, contador_post

    if not os.path.exists(ARQUIVO_ESTADO):
        logger.info("ℹ️ Nenhum arquivo de estado. Iniciando do zero.")
        return

    try:
        with open(ARQUIVO_ESTADO, "r", encoding="utf-8") as f:
            dados = json.load(f)
            ultima_mencao_id = dados.get("ultima_mencao_id")
            ordens_vistas = set(dados.get("ordens_vistas", []))
            contador_post = dados.get("contador_post", 0)
            logger.info(
                f"💾 Estado restaurado: {len(ordens_vistas)} ordens | "
                f"última menção: {ultima_mencao_id} | post #{contador_post}"
            )
    except Exception as e:
        logger.warning(f"⚠️ Erro ao carregar estado: {e}")

# ==========================================================
# 📝 POSTAR (com retry + backoff exponencial)
# ==========================================================
def postar(texto: str, tentativas: int = 3) -> bool:
    if not client:
        logger.error("❌ Cliente do X não inicializado")
        return False

    if len(texto) > MAX_TWEET_LENGTH:
        texto = texto[:MAX_TWEET_LENGTH - 3] + "..."

    for tentativa in range(1, tentativas + 1):
        try:
            response = client.create_tweet(text=texto)
            tweet_id = response.data["id"]
            logger.info(f"✅ Postado (ID: {tweet_id}): {texto[:60]}...")
            return True

        except tweepy.TweepyException as e:
            erro_str = str(e)

            # Conta suspensa/bloqueada
            if "403" in erro_str or "suspended" in erro_str.lower():
                logger.critical("🚨 CONTA BLOQUEADA/SUSPENSA NO X!")
                return False

            # Rate limit — backoff exponencial
            if "429" in erro_str:
                espera = 60 * tentativa
                logger.warning(
                    f"⚠️ Rate limit! Tentativa {tentativa}/{tentativas}. "
                    f"Aguardando {espera}s..."
                )
                time.sleep(espera)
                continue

            # Tweet duplicado
            if "duplicate" in erro_str.lower() or "187" in erro_str:
                logger.warning("⚠️ Tweet duplicado, pulando.")
                return False

            logger.error(f"❌ Erro ao postar (tentativa {tentativa}): {e}")
            return False

        except Exception as e:
            logger.error(f"❌ Erro inesperado: {e}")
            return False

    logger.error(f"❌ Falha após {tentativas} tentativas.")
    return False

# ==========================================================
# 🧠 GERAR POST COM GEMINI
# ==========================================================
def gerar_post(tema: str) -> str:
    fallback = (
        f"📢 BRN — Negocie cripto P2P direto na carteira! "
        f"Sem intermediários, seguro com escrow on-chain.\n🔗 {URL_MURAL}"
    )

    if not model_gemini:
        return fallback

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
        if not response or not response.text:
            return fallback

        texto = response.text.strip().strip('"').strip("'")

        if URL_MURAL not in texto:
            texto = f"{texto}\n{URL_MURAL}"

        if len(texto) > MAX_TWEET_LENGTH:
            texto = texto[:MAX_TWEET_LENGTH - 3 - len(URL_MURAL)] + "...\n" + URL_MURAL

        return texto
    except Exception as e:
        logger.error(f"❌ Erro Gemini: {e}")
        return fallback

# ==========================================================
# ⏰ POST DIÁRIO
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
    global contador_post
    tema = TEMAS[contador_post % len(TEMAS)]
    contador_post += 1
    salvar_estado()

    logger.info(f"📝 Gerando post sobre: {tema[:40]}...")
    texto = gerar_post(tema)
    postar(texto)

# ==========================================================
# 🔍 VERIFICAR MURAL
# ==========================================================
def verificar_mural(browser):
    if not browser or not browser.is_connected():
        logger.error("❌ Chromium não está disponível.")
        return

    context = browser.new_context()
    page = context.new_page()

    try:
        page.route(
            "**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ico,css}",
            lambda r: r.abort()
        )
        page.route("**/*analytics*", lambda r: r.abort())
        page.route("**/*tracker*", lambda r: r.abort())

        logger.info(f"🌐 Acessando mural: {URL_MURAL}")
        page.goto(URL_MURAL, wait_until="domcontentloaded", timeout=30000)

        try:
            page.wait_for_selector(
                "#orders > .order, #orders > .empty",
                timeout=20000
            )
        except Exception:
            logger.warning("⚠️ Timeout esperando ordens renderizarem.")
            return

        ordens = page.query_selector_all("#orders > .order")

        if not ordens:
            logger.info("ℹ️ Nenhuma ordem encontrada no mural.")
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
            time.sleep(5)

    except Exception as e:
        logger.error(f"❌ Erro ao verificar mural: {e}")
    finally:
        salvar_estado()
        context.close()

# ==========================================================
# 💬 RESPONDER MENÇÕES
# ==========================================================
def responder_mencao(tweet_id: str, texto_pergunta: str, autor: str):
    if not texto_pergunta.strip() or not model_gemini:
        return

    prompt = (
        f"{CONTEXTO_BRN}\n\n"
        f"PERGUNTA: {texto_pergunta}\n"
        f"RESPOSTA (máximo {MAX_REPLY_LENGTH} caracteres, amigável e clara):"
    )

    try:
        response = model_gemini.generate_content(prompt)
        if not response or not response.text:
            return

        resposta = response.text.strip()
        if len(resposta) > MAX_REPLY_LENGTH:
            resposta = resposta[:MAX_REPLY_LENGTH - 3] + "..."

        resposta_final = f"@{autor} {resposta}"
        client.create_tweet(text=resposta_final, in_reply_to_tweet_id=tweet_id)
        logger.info(f"💬 Respondido @{autor}: {texto_pergunta[:45]}...")

    except tweepy.TweepyException as e:
        erro_str = str(e)
        if "403" in erro_str or "suspended" in erro_str.lower():
            logger.critical("🚨 CONTA BLOQUEADA/SUSPENSA NO X!")
            return
        if "429" in erro_str:
            logger.warning("⚠️ Rate limit em resposta. Aguardando 60s...")
            time.sleep(60)
            return
        logger.error(f"❌ Erro ao responder: {e}")
    except Exception as e:
        logger.error(f"❌ Erro inesperado: {e}")


def verificar_mencoes():
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
        processou = False

        for tweet in reversed(resposta.data):
            autor_username = usuarios.get(tweet.author_id, "desconhecido")
            texto_limpo = " ".join(
                w for w in tweet.text.split()
                if w.lower() != f"@{MEU_USERNAME}".lower()
            ).strip()

            logger.info(f"📩 Menção de @{autor_username}: {texto_limpo[:50]}...")
            responder_mencao(tweet.id, texto_limpo, autor_username)

            ultima_mencao_id = tweet.id
            processou = True
            time.sleep(3)

        if processou:
            salvar_estado()

    except Exception as e:
        logger.error(f"❌ Erro ao verificar menções: {e}")

# ==========================================================
# 📅 AGENDAMENTOS
# ==========================================================
def agendar_tudo(browser):
    schedule.clear()

    schedule.every().day.at("09:00").do(post_diario)
    schedule.every().day.at("12:00").do(post_diario)
    schedule.every().day.at("15:00").do(post_diario)
    schedule.every().day.at("18:00").do(post_diario)

    schedule.every(45).minutes.do(verificar_mural, browser=browser)
    schedule.every(8).minutes.do(verificar_mencoes)

    logger.info("✅ Agendamentos configurados:")
    logger.info("   • 4 posts/dia (9h, 12h, 15h, 18h)")
    logger.info("   • Verificar mural: a cada 45 min")
    logger.info("   • Verificar menções: a cada 8 min")

# ==========================================================
# 🚀 MAIN (com auto-recovery do Chromium)
# ==========================================================
def main():
    logger.info("🤖 Bot BRN (X) — modo produção")

    if not GEMINI_KEY:
        logger.critical("⛔ GEMINI_KEY não configurada no .env")
        return

    if not inicializar_x():
        logger.critical("⛔ Falha na inicialização do X. Encerrando.")
        return

    carregar_estado()

    with sync_playwright() as p:
        def novo_browser():
            logger.info("🚀 Inicializando Chromium...")
            return p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ]
            )

        browser = novo_browser()
        agendar_tudo(browser)

        try:
            verificar_mural(browser)

            while True:
                schedule.run_pending()

                # Auto-recovery do Chromium
                if not browser.is_connected():
                    logger.warning("⚠️ Chromium caiu! Reiniciando...")
                    try:
                        browser.close()
                    except Exception:
                        pass
                    browser = novo_browser()
                    agendar_tudo(browser)
                    logger.info("✅ Chromium reiniciado e agenda re-registrada.")

                time.sleep(15)

        except KeyboardInterrupt:
            logger.info("👋 Encerrado pelo usuário.")
        except Exception as e:
            logger.error(f"❌ Erro no loop principal: {e}")
        finally:
            logger.info("🧹 Salvando estado e fechando Chromium...")
            salvar_estado()
            try:
                browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()