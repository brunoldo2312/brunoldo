import hashlib
import time
import json
import sqlite3
import os
import sys
import secrets
import requests
import threading
from flask import Flask, jsonify, request, render_template_string

# Configurações Globais da Criptomoeda Bruno
COIN_NAME = "Bruno"
COIN_SYMBOL = "BRN"
TX_EXTRA_BRUNO_MEMO_TAG = 0xBB
DIFFICULTY = 4 

class BrunoWallet:
    """Implementa o sistema de carteiras nos moldes do protocolo CryptoNote (Nerva)"""
    def __init__(self):
        self.spend_secret_key = ""
        self.spend_public_key = ""
        self.view_secret_key = ""
        self.view_public_key = ""
        self.address = ""

    def generate_new_wallet(self):
        """Gera um par de chaves Spend e View e monta o endereço público BRN"""
        self.spend_secret_key = secrets.token_hex(32)
        self.view_secret_key = secrets.token_hex(32)
        
        self.spend_public_key = hashlib.sha256(self.spend_secret_key.encode()).hexdigest()
        self.view_public_key = hashlib.sha256(self.view_secret_key.encode()).hexdigest()
        
        prefixo_brn = "brn1"
        dados_brutos_endereco = f"{prefixo_brn}{self.spend_public_key}{self.view_public_key}"
        checksum = hashlib.sha256(dados_brutos_endereco.encode()).hexdigest()[:8]
        
        self.address = f"{dados_brutos_endereco}{checksum}"
        return self.to_dict()

    def to_dict(self):
        return {
            "address": self.address,
            "spend_secret_key": self.spend_secret_key,
            "spend_public_key": self.spend_public_key,
            "view_secret_key": self.view_secret_key,
            "view_public_key": self.view_public_key
        }

class BrunoMemoProgram:
    def __init__(self, user_name: str = "", message: str = ""):
        self.user_name = user_name
        self.message = message

    def serialize(self) -> str:
        tag_hex = f"{TX_EXTRA_BRUNO_MEMO_TAG:02x}"
        name_bytes = self.user_name.encode('utf-8')
        msg_bytes = self.message.encode('utf-8')
        return tag_hex + f"{len(name_bytes):02x}" + name_bytes.hex() + f"{len(msg_bytes):02x}" + msg_bytes.hex()

    @classmethod
    def deserialize(cls, tx_extra_hex: str):
        try:
            if not tx_extra_hex.startswith(f"{TX_EXTRA_BRUNO_MEMO_TAG:02x}"): return None
            ptr = 2
            name_len = int(tx_extra_hex[ptr:ptr+2], 16)
            ptr += 2
            name = bytes.fromhex(tx_extra_hex[ptr:ptr+(name_len*2)]).decode('utf-8')
            ptr += name_len * 2
            msg_len = int(tx_extra_hex[ptr:ptr+2], 16)
            ptr += 2
            msg = bytes.fromhex(tx_extra_hex[ptr:ptr+(msg_len*2)]).decode('utf-8')
            return cls(user_name=name, message=msg)
        except: return None

class BrunoBlock:
    def __init__(self, index, previous_hash, transactions, tx_extra, timestamp=None, nonce=0, block_hash=None):
        self.index = index
        self.timestamp = timestamp if timestamp else time.time()
        self.previous_hash = previous_hash
        self.transactions = transactions if isinstance(transactions, list) else json.loads(transactions)
        self.tx_extra = tx_extra
        self.nonce = nonce
        self.hash = block_hash if block_hash else self.calculate_hash()

    def calculate_hash(self) -> str:
        block_string = json.dumps({
            "index": self.index, "timestamp": self.timestamp, "previous_hash": self.previous_hash,
            "transactions": self.transactions, "tx_extra": self.tx_extra, "nonce": self.nonce
        }, sort_keys=True).encode()
        return hashlib.sha256(block_string).hexdigest()

    def mine_block(self):
        target = "0" * DIFFICULTY
        print(f"⛏️  Minerando bloco real {self.index}... (Algoritmo CPU Nerva)")
        while self.hash[:DIFFICULTY] != target:
            if not IS_MINING:
                print("🛑 Mineração interrompida pelo usuário.")
                return False
            self.nonce += 1
            self.hash = self.calculate_hash()
        print(f"✅ Bloco {self.index} Minerado com sucesso! Hash: {self.hash}")
        return True

    def to_dict(self):
        return {
            "index": self.index, "timestamp": self.timestamp, "previous_hash": self.previous_hash,
            "transactions": self.transactions, "tx_extra": self.tx_extra, "nonce": self.nonce, "hash": self.hash
        }

class RealBrunoBlockchain:
    def __init__(self, port):
        self.port = port
        self.db_path = f"blockchain_node_{port}.db"
        self.peers = set()
        self.init_database()

    def init_database(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS blocks (
                    id_index INTEGER PRIMARY KEY, timestamp REAL, previous_hash TEXT,
                    transactions TEXT, tx_extra TEXT, nonce INTEGER, hash TEXT
                )
            ''')
            cursor.execute('SELECT COUNT(*) FROM blocks')
            if cursor.fetchone() == 0:
                memo_genesis = BrunoMemoProgram("Bruno", "Bloco Genesis - Moeda Bruno com Chaves Nerva Ativas.")
                genesis_block = BrunoBlock(0, "0", ["Genesis Tx"], memo_genesis.serialize())
                self.save_block_to_db(genesis_block)

    def save_block_to_db(self, block):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO blocks (id_index, timestamp, previous_hash, transactions, tx_extra, nonce, hash)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (block.index, block.timestamp, block.previous_hash, json.dumps(block.transactions), block.tx_extra, block.nonce, block.hash))
            conn.commit()

    def get_latest_block(self) -> BrunoBlock:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id_index, previous_hash, transactions, tx_extra, timestamp, nonce, hash FROM blocks ORDER BY id_index DESC LIMIT 1')
            row = cursor.fetchone()
            if row:
                return BrunoBlock(
                    index=row[0],
                    previous_hash=row[1],
                    transactions=json.loads(row[2]),
                    tx_extra=row[3],
                    timestamp=row[4],
                    nonce=row[5],
                    block_hash=row[6]
                )
            return None

    def get_full_chain(self):
        chain = []
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id_index, previous_hash, transactions, tx_extra, timestamp, nonce, hash FROM blocks ORDER BY id_index ASC')
            for row in cursor.fetchall():
                block_data = BrunoBlock(
                    index=row[0],
                    previous_hash=row[1],
                    transactions=json.loads(row[2]),
                    tx_extra=row[3],
                    timestamp=row[4],
                    nonce=row[5],
                    block_hash=row[6]
                ).to_dict()
                chain.append(block_data)
        return chain

    def create_and_mine_block(self, author_name, memo_text, transactions):
        latest = self.get_latest_block()
        memo = BrunoMemoProgram(author_name, memo_text)
        new_block = BrunoBlock(latest.index + 1, latest.hash, transactions, memo.serialize())
        sucesso = new_block.mine_block()
        if sucesso:
            self.save_block_to_db(new_block)
            return new_block
        return None

# =====================================================================
# API DO NÓ VALIDADOR (FLASK WEB SERVER)
# =====================================================================
app = Flask(__name__)

PORT = 5001

if "--port" in sys.argv:
    idx = sys.argv.index("--port")
    PORT = int(sys.argv[idx + 1])
elif len(sys.argv) > 1 and sys.argv[1].isdigit():
    PORT = int(sys.argv[1])

blockchain = RealBrunoBlockchain(PORT)

# Controle Global do Minerador
IS_MINING = False

def loop_minerador_automatico():
    """Loop executado em uma Thread paralela para minerar blocos sem parar"""
    global IS_MINING
    print("🚀 Thread do Minerador Inicializada.")
    
    while IS_MINING:
        try:
            latest = blockchain.get_latest_block()
            txs = [f"Recompensa Automatica para No {PORT}"]
            memo = BrunoMemoProgram(f"No_{PORT}", "Mineracao Continua Ativada via Painel.")
            
            new_block = BrunoBlock(latest.index + 1, latest.hash, txs, memo.serialize())
            
            print(f"⛏️  [Minerador Web] Minerando Bloco {new_block.index}...")
            if new_block.mine_block():
                blockchain.save_block_to_db(new_block)
                print(f"✨ [Minerador Web] Bloco {new_block.index} adicionado!")
            
            time.sleep(2)
        except Exception as e:
            print(f"❌ Erro no minerador: {e}")
            time.sleep(5)

# Interface Grafica em HTML integrada com fechamento correto das aspas triplas
HTML_PANEL = """
<!DOCTYPE html>
<html>
<head>
    <title>Bruno Coin - Painel do No</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #121212; color: #e0e0e0; margin: 40px; }
        .container { max-width: 600px; margin: auto; background: #1e1e1e; padding: 30px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); text-align: center; }
        h1 { color: #ff9800; margin-bottom: 5px; }
        .status { font-size: 1.2em; margin: 20px 0; font-weight: bold; }
        .status-off { color: #f44336; }
        .status-on { color: #4caf50; animation: pulse 1.5s infinite; }
        button { font-size: 1.1em; padding: 12px 30px; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; transition: 0.2s; width: 80%; }
        .btn-start { background-color: #4caf50; color: white; }
