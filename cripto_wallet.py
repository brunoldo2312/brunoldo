# cripto_wallet.py
# Criptografia profissional: ECDSA (SECP256K1) + AES-256-GCM + Argon2id
# Migrado de `ecdsa` para `cryptography` (mantendo compatibilidade raw 64B)
import hashlib
import json
import secrets
import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature, encode_dss_signature
)
from cryptography.hazmat.primitives import hashes
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from argon2.low_level import hash_secret_raw, Type


_SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_SECP256K1_HALF_N = _SECP256K1_N // 2


def _raw_to_der(sig_raw: bytes) -> bytes:
    if len(sig_raw) != 64:
        raise ValueError("Assinatura raw deve ter 64 bytes")
    r = int.from_bytes(sig_raw[:32], "big")
    s = int.from_bytes(sig_raw[32:], "big")
    return encode_dss_signature(r, s)


def _der_to_raw(sig_der: bytes) -> bytes:
    r, s = decode_dss_signature(sig_der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def _normalize_low_s(sig_raw: bytes) -> bytes:
    r = int.from_bytes(sig_raw[:32], "big")
    s = int.from_bytes(sig_raw[32:], "big")
    if s > _SECP256K1_HALF_N:
        s = _SECP256K1_N - s
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def _pubkey_raw_to_ec(public_key_raw: bytes) -> ec.EllipticCurvePublicKey:
    if len(public_key_raw) != 64:
        raise ValueError("Pubkey raw deve ter 64 bytes")
    encoded = b"\x04" + public_key_raw
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256K1(), encoded)


def _pubkey_ec_to_raw(pub: ec.EllipticCurvePublicKey) -> bytes:
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat
    )
    uncompressed = pub.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return uncompressed[1:]


class WalletManager:
    ARGON2_TIME_COST = 3
    ARGON2_MEMORY_COST = 65536
    ARGON2_PARALLELISM = 4
    ARGON2_HASH_LEN = 32
    ARGON2_SALT_LEN = 16
    GCM_NONCE_LEN = 12

    @staticmethod
    def address_from_public_key(public_key_hex: str) -> str:
        public_key = bytes.fromhex(public_key_hex)
        _pubkey_raw_to_ec(public_key)
        digest = hashlib.sha256(public_key).hexdigest()
        return f"brn1{digest[:40]}"

    @staticmethod
    def generate_keypair() -> dict:
        private_key = ec.generate_private_key(ec.SECP256K1())
        public_key = private_key.public_key()
        sk_hex = private_key.private_numbers().private_value.to_bytes(32, "big").hex()
        vk_hex = _pubkey_ec_to_raw(public_key).hex()
        address = WalletManager.address_from_public_key(vk_hex)
        return {
            "address": address,
            "spend_secret_key": sk_hex,
            "public_key": vk_hex,
        }

    @staticmethod
    def sign_transaction(private_key_hex: str, message_dict: dict) -> str:
        priv_int = int(private_key_hex, 16)
        private_key = ec.derive_private_key(priv_int, ec.SECP256K1())
        msg_bytes = json.dumps(
            message_dict, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        sig_der = private_key.sign(msg_bytes, ec.ECDSA(hashes.SHA256()))
        sig_raw = _der_to_raw(sig_der)
        sig_raw = _normalize_low_s(sig_raw)
        return sig_raw.hex()

    @staticmethod
    def verify_signature(public_key_hex: str, message_dict: dict,
                         signature_hex: str) -> bool:
        try:
            public_key = _pubkey_raw_to_ec(bytes.fromhex(public_key_hex))
            msg_bytes = json.dumps(
                message_dict, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            sig_raw = bytes.fromhex(signature_hex)
            if len(sig_raw) != 64:
                return False
            sig_der = _raw_to_der(sig_raw)
            public_key.verify(sig_der, msg_bytes, ec.ECDSA(hashes.SHA256()))
            return True
        except (InvalidSignature, ValueError, Exception):
            return False

    @staticmethod
    def _wallet_dir() -> Path:
        d = Path.cwd() / "wallets"
        d.mkdir(mode=0o700, exist_ok=True)
        return d

    @classmethod
    def _wallet_path(cls, filename: str) -> Path:
        safe_name = Path(filename).name
        if not safe_name or safe_name in {".", ".."}:
            raise ValueError("Nome de arquivo de carteira inválido.")
        if not safe_name.endswith(".wallet"):
            safe_name += ".wallet"
        return cls._wallet_dir() / safe_name

    @classmethod
    def save_encrypted_wallet(cls, filename, password, address,
                              spend_secret_key, public_key=""):
        try:
            if not isinstance(password, str) or len(password) < 12:
                return {"status": "erro",
                        "message": "Use uma senha com pelo menos 12 caracteres."}
            filename = cls._wallet_path(filename)
            wallet_data = {
                "address": address,
                "spend_secret_key": spend_secret_key,
                "public_key": public_key,
            }
            raw_json = json.dumps(wallet_data).encode("utf-8")
            salt = secrets.token_bytes(cls.ARGON2_SALT_LEN)
            key = hash_secret_raw(
                secret=password.encode(),
                salt=salt,
                time_cost=cls.ARGON2_TIME_COST,
                memory_cost=cls.ARGON2_MEMORY_COST,
                parallelism=cls.ARGON2_PARALLELISM,
                hash_len=cls.ARGON2_HASH_LEN,
                type=Type.ID,
            )
            nonce = secrets.token_bytes(cls.GCM_NONCE_LEN)
            aesgcm = AESGCM(key)
            ciphertext = aesgcm.encrypt(nonce, raw_json, None)
            payload = {
                "version": 2,
                "method": "aes-256-gcm-argon2id",
                "argon2": {
                    "time_cost": cls.ARGON2_TIME_COST,
                    "memory_cost": cls.ARGON2_MEMORY_COST,
                    "parallelism": cls.ARGON2_PARALLELISM,
                    "hash_len": cls.ARGON2_HASH_LEN,
                },
                "salt": base64.b64encode(salt).decode(),
                "nonce": base64.b64encode(nonce).decode(),
                "ciphertext": base64.b64encode(ciphertext).decode(),
            }
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            try:
                os.chmod(filename, 0o600)
            except OSError:
                pass
            return {"status": "sucesso",
                    "message": f"Carteira salva com seguranca em {filename}"}
        except Exception as e:
            return {"status": "erro", "message": str(e)}

    @classmethod
    def load_encrypted_wallet(cls, filename, password):
        try:
            filename = cls._wallet_path(filename)
            if not filename.exists():
                return {"status": "erro", "message": "Arquivo nao encontrado."}
            with open(filename, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if payload.get("method") != "aes-256-gcm-argon2id":
                return {"status": "erro",
                        "message": "Formato antigo/inseguro. Reexporte a carteira."}
            salt = base64.b64decode(payload["salt"])
            nonce = base64.b64decode(payload["nonce"])
            ciphertext = base64.b64decode(payload["ciphertext"])
            p = payload["argon2"]
            key = hash_secret_raw(
                secret=password.encode(),
                salt=salt,
                time_cost=p["time_cost"],
                memory_cost=p["memory_cost"],
                parallelism=p["parallelism"],
                hash_len=p["hash_len"],
                type=Type.ID,
            )
            aesgcm = AESGCM(key)
            try:
                decrypted = json.loads(aesgcm.decrypt(nonce, ciphertext, None).decode())
            except Exception:
                return {"status": "erro", "message": "Senha incorreta."}
            return {
                "status": "sucesso",
                "address": decrypted["address"],
                "spend_secret_key": decrypted["spend_secret_key"],
                "public_key": decrypted.get("public_key", ""),
            }
        except Exception as e:
            return {"status": "erro", "message": f"Erro ao carregar carteira: {e}"}
