# app_wallet.py — GUI desktop da carteira RWA funcional com conexões ativas.
import json
import os
import time
import requests
import webview
from cripto_wallet import WalletManager

# Configurações de API unificadas com o .env (Conecta no nó que está ativo na porta 5000)
API = "http://127.0.0.1:5000"
WEB_PASS = os.environ.get("BRN_WEB_PASS", "bruno123")
AUTH = ("admin", WEB_PASS)

class Api:
    def generate_wallet(self):
        """Gera e retorna chaves assimétricas estruturadas via SECP256k1"""
        try:
            return WalletManager.generate_keypair()
        except Exception as e:
            return {"erro": str(e)}

    def portfolio(self, address):
        """Busca o portfólio completo direto do nó local na porta 5000"""
        try:
            r = requests.get(f"{API}/api/portfolio/{address}", auth=AUTH, timeout=5)
            if r.status_code == 401:
                return {"erro": "Erro de Autenticação (401): Verifique a senha do painel web."}
            return r.json().get("portfolio", {})
        except Exception as e:
            return {"erro": str(e)}

    def transfer(self, sender, to, asset_id, amount, sk, pk):
        """Cria, assina e transmite uma transação criptográfica legítima via API"""
        try:
            r = requests.get(f"{API}/api/portfolio/{sender}", auth=AUTH, timeout=5)
            if r.status_code == 401:
                return {"ok": False, "msg": "Erro 401: Falha na autenticação com o nó."}
                
            if asset_id == "KYC":
                return {"ok": False, "msg": "Use as abas administrativas para gerenciar KYC."}

            generated_nonce = int(time.time() * 1000)
            
            payload = {
                "asset_id": asset_id,
                "from": sender,
                "to": to,
                "amount": float(amount),
                "public_key": pk,
                "private_key": sk,
                "nonce": generated_nonce
            }
            
            tx_r = requests.post(f"{API}/api/transfer", auth=AUTH, json=payload, timeout=5)
            return tx_r.json()
            
        except Exception as e:
            return {"ok": False, "msg": f"Erro de processamento: {str(e)}"}

    def mine_block(self, validator_address):
        """Força o nó local a produzir e forjar um Executa mineração distribuída via API HTTP"""
        try:
            if not validator_address:
                return {"ok": False, "msg": "Defina o endereço do validador (sua carteira) para receber a recompensa."}
            
            payload = {"validator_address": validator_address}
            r = requests.post(f"{API}/api/mine", auth=AUTH, json=payload, timeout=10)
            
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 401:
                return {"ok": False, "msg": "Erro 401: Falha na autenticação com o nó."}
            else:
                return {"ok": False, "msg": f"Erro de processamento no nó: Código {r.status_code}"}
                
        except Exception as e:
            return {"ok": False, "msg": f"Erro de conexão de rede com o nó para mineração: {str(e)}"}

    def save_wallet(self, filename, password, address, sk, pk):
        """Salva a carteira no disco usando Argon2id + AES-256-GCM"""
        return WalletManager.save_encrypted_wallet(filename, password, address, sk, pk)

    def load_wallet(self, filename, password):
        """Restaura e descriptografa a carteira a partir da pasta wallets/"""
        return WalletManager.load_encrypted_wallet(filename, password)

if __name__ == "__main__":
    api = Api()
    webview.create_window(
        "BRN RWA — Carteira Digital Core", 
        url=f"file://{os.path.abspath('index.html')}",
        js_api=api, 
        width=980, 
        height=840
    )
    webview.start(gui='qt')
