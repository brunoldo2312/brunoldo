# descoberta_p2p.py — Motor Híbrido de Descoberta Automática (Local Multicast + Internet Tracker)
import socket
import threading
import time
import os
import requests

MULTICAST_GROUP = '239.255.255.250'
MULTICAST_PORT = 50007

class AutoNodeDiscovery:
    def __init__(self, p2p_port=7777):
        self.p2p_port = p2p_port
        self.discovered_peers = set()
        self.running = True
        self.tracker_url = os.environ.get("BRN_TRACKER_URL", "http://127.0.0")

    def _get_local_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
        except Exception:
            ip = '127.0.0.1'
        finally:
            s.close()
        return ip

    def start_server(self):
        local_ip = self._get_local_ip()
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(('', MULTICAST_PORT))
        
        mreq = socket.inet_aton(MULTICAST_GROUP) + socket.inet_aton('0.0.0.0')
        server_socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        
        print(f"📡 Buscador Automático Local Ativo! IP: {local_ip}")
        
        while self.running:
            try:
                server_socket.settimeout(2.0)
                data, addr = server_socket.recvfrom(1024)
                msg = data.decode('utf-8')
                
                if msg.startswith("BRN_NODE_PING:"):
                    remote_p2p_port = msg.split(":")[1]
                    remote_ip = addr[0]
                    
                    if remote_ip != local_ip:
                        peer_address = f"{remote_ip}:{remote_p2p_port}"
                        if peer_address not in self.discovered_peers:
                            self.discovered_peers.add(peer_address)
                            print(f"✨ [P2P Wi-Fi] Novo computador localizado em casa: {peer_address}")
                            
                            import node
                            if node._global_node_ref:
                                node._global_node_ref.register_external_peer(peer_address)
                            
                            response_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                            response_sock.sendto(f"BRN_NODE_PONG:{self.p2p_port}".encode('utf-8'), addr)
                            response_sock.close()
            except socket.timeout:
                continue
            except Exception:
                pass

    def start_client_broadcast(self):
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        client_socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        while self.running:
            try:
                msg = f"BRN_NODE_PING:{self.p2p_port}"
                client_socket.sendto(msg.encode('utf-8'), (MULTICAST_GROUP, MULTICAST_PORT))
                time.sleep(5)
            except Exception:
                time.sleep(5)

    def start_internet_tracker_sync(self):
        local_ip = self._get_local_ip()
        while self.running:
            try:
                payload = {"p2p_port": self.p2p_port, "local_ip": local_ip}
                r = requests.post(self.tracker_url, json=payload, timeout=5)
                if r.status_code == 200:
                    external_peers = r.json().get("peers", [])
                    for peer in external_peers:
                        if peer not in self.discovered_peers:
                            import node
                            if node._global_node_ref:
                                node._global_node_ref.register_external_peer(peer)
            except Exception:
                pass
            time.sleep(20)

    def run(self):
        threading.Thread(target=self.start_server, daemon=True).start()
        threading.Thread(target=self.start_client_broadcast, daemon=True).start()
        threading.Thread(target=self.start_internet_tracker_sync, daemon=True).start()
