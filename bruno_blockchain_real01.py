import hashlib
import json
from time import time
from flask import Flask, jsonify, request

class Blockchain:
    def __init__(self):
        self.chain = []
        self.current_transactions = []
        # Criação do bloco gênese
        self.new_block(previous_hash='1', proof=100)

    def new_block(self, proof, previous_hash=None):
        """Cria um novo bloco na Blockchain"""
        block = {
            'index': len(self.chain) + 1,
            'timestamp': time(),
            'transactions': self.current_transactions,
            'proof': proof,
            'previous_hash': previous_hash or self.hash(self.chain[-1]),
        }
        self.current_transactions = []
        self.chain.append(block)
        return block

    def new_transaction(self, sender, recipient, amount):
        """Adiciona uma nova transação à lista"""
        self.current_transactions.append({
            'sender': sender,
            'recipient': recipient,
            'amount': amount,
        })
        return self.last_block['index'] + 1

    @property
    def last_block(self):
        return self.chain[-1]

    @staticmethod
    def hash(block):
        """Cria um hash SHA-256 de um bloco"""
        block_string = json.dumps(block, sort_keys=True).encode()
        return hashlib.sha256(block_string).hexdigest()

    def proof_of_work(self, last_proof):
        """Algoritmo simples de Prova de Trabalho (PoW)"""
        proof = 0
        while self.valid_proof(last_proof, proof) is False:
            proof += 1
        return proof

    @staticmethod
    def valid_proof(last_proof, proof):
        """Valida a prova: o hash contém 4 zeros iniciais?"""
        guess = f'{last_proof}{proof}'.encode()
        guess_hash = hashlib.sha256(guess).hexdigest()
        return guess_hash[:4] == "0000"

# Inicializa o nó Flask
app = Flask(__name__)

# Inicializa a Blockchain
blockchain = Blockchain()

# ==============================================================================
# CORREÇÃO DA LINHA 230: STRING TRIPLE-QUOTED FECHADA CORRETAMENTE
# ==============================================================================
HTML_PANEL = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bruno Crypto Blockchain Panel</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 40px; background-color: #0e1118; color: #ffffff; }
        .container { max-width: 800px; margin: 0 auto; background: #161b22; padding: 30px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.5); border: 1px solid #30363d; }
        h1 { color: #58a6ff; margin-top: 0; }
        .status { display: inline-block; padding: 6px 12px; background-color: #238636; color: white; border-radius: 20px; font-size: 14px; font-weight: bold; }
        .endpoints { margin-top: 25px; background: #0d1117; padding: 15px; border-radius: 8px; border: 1px solid #21262d; }
        code { color: #ff7b72; font-family: monospace; font-size: 15px; }
        ul { padding-left: 20px; line-height: 1.8; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 BrunoCrypto Node Panel</h1>
        <p>Status do Servidor: <span class="status">ONLINE</span></p>
        <p>O nó da sua blockchain está rodando com sucesso na porta <strong>5001</strong>.</p>
        
        <div class="endpoints">
            <h3>🔗 Rotas da API Disponíveis:</h3>
            <ul>
                <li>Visualizar a Blockchain: <code>GET /chain</code></li>
                <li>Minerar um Bloco: <code>GET /mine</code></li>
                <li>Criar Transação: <code>POST /transactions/new</code></li>
            </ul>
        </div>
    </div>
</body>
</html>
""" # <--- AQUI ESTÁ O FECHAMENTO QUE EVITA O SYNTAXERROR!

@app.route('/', methods=['GET'])
def index():
    return HTML_PANEL

@app.route('/mine', methods=['GET'])
def mine():
    last_block = blockchain.last_block
    last_proof = last_block['proof']
    proof = blockchain.proof_of_work(last_proof)

    # Recompensa por minerar (remetente "0" significa que é uma moeda minerada)
    blockchain.new_transaction(
        sender="0",
        recipient="bruno_node_address",
        amount=1,
    )

    previous_hash = blockchain.hash(last_block)
    block = blockchain.new_block(proof, previous_hash)

    response = {
        'message': "Novo bloco minerado com sucesso!",
        'index': block['index'],
        'transactions': block['transactions'],
        'proof': block['proof'],
        'previous_hash': block['previous_hash'],
    }
    return jsonify(response), 200

@app.route('/transactions/new', methods=['POST'])
def new_transaction():
    values = request.get_json()
    required = ['sender', 'recipient', 'amount']
    if not values or not all(k in values for k in required):
        return 'Dados faltando', 400

    index = blockchain.new_transaction(values['sender'], values['recipient'], values['amount'])
    response = {'message': f'Transação será adicionada ao Bloco {index}'}
    return jsonify(response), 201

@app.route('/chain', methods=['GET'])
def full_chain():
    response = {
        'chain': blockchain.chain,
        'length': len(blockchain.chain),
    }
    return jsonify(response), 200

if __name__ == '__main__':
    app.run(port=5001, debug=True)
