# node.py — Core Node Híbrido unificado com Consenso RWA, Descoberta UDP e Sincronização Extra-Residencial
import hashlib
import json
import time
import secrets
import threading
import sqlite3
import os
import socket
import requests
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Set, Tuple
from functools import wraps
from flask import Flask, jsonify, request, Response

# ---------------------------------------------------------------- env
NETWORK_ID = os.environ.get("BRN_NETWORK_ID", "brn-rwa-1")
P2P_HOST = os.environ.get("BRN_P2P_HOST", "0.0.0.0")
P2P_PORT = int(os.environ.get("BRN_P2P_PORT", "7777"))

_seed_env = os.environ.get("BRN_SEED_PEERS", "").strip()
SEED_PEERS = [p.strip() for p in _seed_env.split(",") if p.strip() and ":" in p]

TARGET_BLOCK_TIME = int(os.environ.get("BRN_TARGET_BLOCK_TIME", "10"))
BLOCK_REWARD = float(os.environ.get("BRN_BLOCK_REWARD", "1"))
MIN_STAKE = float(os.environ.get("BRN_MIN_STAKE", "100"))
DB_PATH = os.environ.get("BRN_DB_PATH", "blockchain.db")

FINALITY_INTERVAL = int(os.environ.get("BRN_FINALITY_INTERVAL", "5"))
FINALITY_THRESHOLD = float(os.environ.get("BRN_FINALITY_THRESHOLD", "0.67"))

FAUCET_ADDRESS = os.environ.get("BRN_FAUCET_ADDRESS", "").strip()
FAUCET_AMOUNT = float(os.environ.get("BRN_FAUCET_AMOUNT", "100"))
FAUCET_COOLDOWN = int(os.environ.get("BRN_FAUCET_COOLDOWN", "3600"))

REGULATOR_ADDRESS = os.environ.get("BRN_REGULATOR_ADDRESS", "").strip()

MULTICAST_GROUP = '239.255.255.250'
MULTICAST_PORT = 50007

WEB_USER = os.environ.get("BRN_WEB_USER", "admin")
WEB_PASS = os.environ.get("BRN_WEB_PASS", "bruno123")

GENESIS_ALLOCATIONS: Dict[str, Dict[str, float]] = {}
NATIVE_ASSET = "BRN"

# ---------------------------------------------------------------- Flask Server Setup
app = Flask(__name__)
_blockchain_instance = None

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or auth.username != WEB_USER or auth.password != WEB_PASS:
            return Response("Acesso negado.", 401, {"WWW-Authenticate": 'Basic realm="BRN RWA"'})
        return f(*args, **kwargs)
    return decorated

# ==================================================================
# Estruturas da Blockchain RWA
# ==================================================================
@dataclass
class Block:
    index: int
    timestamp: float
    previous_hash: str
    transactions: List[dict]
    validator: str
    validator_public_key: str = ""
    nonce: int = 0
    signature: str = ""
    hash: str = ""
    registry_snapshot: Optional[dict] = None

    def calculate_hash(self) -> str:
        body = {
            "index": self.index,
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
            "transactions": self.transactions,
            "validator": self.validator,
            "validator_public_key": self.validator_public_key,
            "nonce": self.nonce,
            "network_id": NETWORK_ID,
            "registry_hash": self._registry_hash(),
        }
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha3_256(raw).hexdigest()

    def _registry_hash(self) -> str:
        if not self.registry_snapshot: return ""
        raw = json.dumps(self.registry_snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha3_256(raw).hexdigest()

    def finalize(self):
        self.hash = self.calculate_hash()

    def to_dict(self) -> dict: return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Block": return cls(**d)

class State:
    def __init__(self):
        self.balances: Dict[str, Dict[str, float]] = {}
        self.nonces: Dict[str, int] = {}
        self.frozen: Dict[str, Dict[str, float]] = {}
        self.total_supply: Dict[str, float] = {}

    def balance(self, addr: str, asset_id: str = NATIVE_ASSET) -> float: return self.balances.get(addr, {}).get(asset_id, 0.0)
    def available(self, addr: str, asset_id: str) -> float: return self.balance(addr, asset_id) - self.frozen.get(addr, {}).get(asset_id, 0.0)
    def nonce(self, addr: str) -> int: return self.nonces.get(addr, 0)

    def credit(self, addr: str, asset_id: str, amount: float):
        self.balances.setdefault(addr, {})
        self.balances[addr][asset_id] = self.balances[addr].get(asset_id, 0.0) + amount
        self.total_supply[asset_id] = self.total_supply.get(asset_id, 0.0) + amount

    def debit(self, addr: str, asset_id: str, amount: float):
        self.balances.setdefault(addr, {})
        self.balances[addr][asset_id] = self.balances[addr].get(asset_id, 0.0) - amount
        self.total_supply[asset_id] = self.total_supply.get(asset_id, 0.0) - amount

    def copy(self) -> "State":
        s = State()
        s.balances = {a: dict(v) for a, v in self.balances.items()}
        s.nonces = dict(self.nonces)
        s.frozen = {a: dict(v) for a, v in self.frozen.items()}
        s.total_supply = dict(self.total_supply)
        return s

class Blockchain:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.node_identity = None
        self.chain: List[Block] = []
        self.pending: List[dict] = []
        self.state = State()
        self.lock = threading.RLock()
        self._init_db()
        if not self._load_from_db():
            self._create_genesis()
            self._persist_block(self.chain[0])
            self._rebuild_state()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS blocks (idx INTEGER PRIMARY KEY, hash TEXT NOT NULL, validator TEXT, timestamp REAL, data TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS mempool (tx_hash TEXT PRIMARY KEY, ts REAL NOT NULL, data TEXT NOT NULL)")

    def _persist_block(self, block: Block):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO blocks (idx, hash, validator, timestamp, data) VALUES (?, ?, ?, ?, ?)", (block.index, block.hash, block.validator, block.timestamp, json.dumps(block.to_dict())))

    def _load_from_db(self) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT data FROM blocks ORDER BY idx ASC").fetchall()
        if not rows: return False
        self.chain = [Block.from_dict(json.loads(r[0])) for r in rows]
        self._rebuild_state()
        return True

    def _rebuild_state(self):
        self.state = State()
        for blk in self.chain[1:]:
            for tx in blk.transactions: self._apply_tx_to_state(self.state, tx)
            self.state.credit(blk.validator, NATIVE_ASSET, BLOCK_REWARD)

    def _apply_tx_to_state(self, state: State, tx: dict) -> bool:
        sender, receiver, asset_id, amount = tx["from"], tx["to"], tx["asset_id"], float(tx["amount"])
        state.debit(sender, asset_id, amount)
        state.credit(receiver, asset_id, amount)
        return True

    def _create_genesis(self):
        genesis = Block(index=0, timestamp=time.time(), previous_hash="0" * 64, transactions=[], validator="genesis")
        genesis.finalize()
        self.chain.append(genesis)

    @property
    def last_block(self) -> Block: return self.chain[-1]

    def add_transaction(self, tx: dict) -> dict:
        with self.lock:
            self.pending.append(tx)
            return {"ok": True, "msg": "Transação aceita na mempool."}

    def produce_block(self, validator_addr="Node_Local") -> Optional[Block]:
        with self.lock:
            block = Block(index=self.last_block.index + 1, timestamp=time.time(), previous_hash=self.last_block.hash, transactions=list(self.pending), validator=validator_addr)
            block.finalize()
            self.chain.append(block)
            self._persist_block(block)
            self.pending.clear()
            self._rebuild_state()
            return block

    def replace_chain(self, new_chain: List[dict]) -> bool:
        try:
            blocks = [Block.from_dict(b) if isinstance(b, dict) else b for b in new_chain]
        except Exception: return False
        with self.lock:
            if len(blocks) <= len(self.chain): return False
            with sqlite3.connect(self.db_path) as conn: conn.execute("DELETE FROM blocks")
            self.chain = blocks
            for blk in self.chain: self._persist_block(blk)
            self._rebuild_state()
            print(f"📥 [P2P Sync] Cadeia sincronizada externa! Altura: {len(self.chain)}")
            return True

    def portfolio(self, addr: str) -> dict:
        bal = self.state.balance(addr)
        return {NATIVE_ASSET: {"amount": bal, "available": bal, "frozen": 0.0, "asset": None, "compliance": None}}

# ==================================================================
# API Endpoints (Flask) para comunicação com a Carteira
# ==================================================================
@app.route("/api/portfolio/<address>")
@require_auth
def api_portfolio(address):
    return jsonify({"portfolio": _blockchain_instance.portfolio(address)})

@app.route("/api/faucet", methods=["POST"])
@require_auth
def api_faucet():
    data = request.get_json(silent=True) or {}
    addr = data.get("address", "").strip()
    if not addr: return jsonify(ok=False, msg="Endereço ausente"), 400
    tx = {"type": "transfer", "asset_id": NATIVE_ASSET, "from": "faucet_master", "to": addr, "amount": 100.0, "nonce": int(time.time()), "public_key": "system", "signature": "system"}
    _blockchain_instance.add_transaction(tx)
    return jsonify(ok=True, msg="100 BRN adicionados à fila de espera da mempool.")

@app.route("/api/transfer", methods=["POST"])
@require_auth
def api_transfer():
    data = request.get_json(silent=True) or {}
