import socket
import threading
import random
import pickle
import struct
import time
from collections import deque

HOST = '0.0.0.0'
PORT = 5555

RANKS = [str(n) for n in range(2, 11)] + ['J', 'Q', 'K', 'A']
SUITS = ['Hearts', 'Diamonds', 'Clubs', 'Spades']
CARD_VALUES = {str(n): n for n in range(2, 11)}
CARD_VALUES.update({'J': 11, 'Q': 12, 'K': 13, 'A': 14})

def create_deck():
    return [(rank, suit) for suit in SUITS for rank in RANKS]

def card_value(card):
    return CARD_VALUES[card[0]]

def send_msg(sock, data):
    try:
        msg = pickle.dumps(data)
        msg = struct.pack('>I', len(msg)) + msg
        sock.sendall(msg)
        return True
    except Exception as e:
        print(f"Send failed: {e}")
        try:
            sock.close()
        except:
            pass
        return False

def recv_msg(sock):
    try:
        raw_msglen = recvall(sock, 4)
        if not raw_msglen:
            return None
        msglen = struct.unpack('>I', raw_msglen)[0]
        return pickle.loads(recvall(sock, msglen))
    except Exception as e:
        print(f"Receive failed: {e}")
        return None

def recvall(sock, n):
    data = bytearray()
    while len(data) < n:
        try:
            packet = sock.recv(n - len(data))
            if not packet:
                return None
            data.extend(packet)
        except socket.timeout:
            print("Socket recv timeout")
            return None
        except Exception as e:
            print(f"Socket recv error: {e}")
            return None
    return data

class WarGameServer:
    def __init__(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((HOST, PORT))
        self.server_socket.listen(5)
        print(f"Server listening on {HOST}:{PORT}")

        self.clients = [None, None]
        self.client_names = [None, None]
        self.name_to_index = {}
        # Use deque for efficient pop from front
        self.stacks = [deque(), deque()]
        self.winning_piles = [deque(), deque()]
        self.ready_flags = [threading.Event(), threading.Event()]
        self.client_threads = []
        self.heartbeat_times = [time.time(), time.time()]
        self.reconnect_deadlines = [None, None]
        self.disconnected = threading.Event()
        self.game_started = False
        self.current_round = 0
        self.heartbeat_interval = 20  # Increased from 15 for more tolerance
        self.reconnect_timeout = 120
        self.udp_socket = None
        self.broadcast_thread = None

    def start_udp_broadcast(self):
        def broadcast():
            try:
                self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                try:
                    # Try to get actual IP, fallback to localhost
                    local_ip = socket.gethostbyname(socket.gethostname())
                    if local_ip.startswith('127.'):
                        # If we got localhost, try a different approach
                        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                            s.connect(("8.8.8.8", 80))
                            local_ip = s.getsockname()[0]
                except:
                    local_ip = '127.0.0.1'
                
                message = f"{local_ip}:{PORT}".encode()

                while not self.disconnected.is_set():
                    try:
                        self.udp_socket.sendto(message, ('<broadcast>', 54545))
                    except Exception as e:
                        print(f"Broadcast error: {e}")
                        break
                    time.sleep(2)
            except Exception as e:
                print(f"UDP broadcast setup failed: {e}")
            finally:
                if self.udp_socket:
                    try:
                        self.udp_socket.close()
                    except:
                        pass

        self.broadcast_thread = threading.Thread(target=broadcast, daemon=True)
        self.broadcast_thread.start()

    def wait_for_clients(self):
        connected_players = 0
        while connected_players < 2:
            try:
                conn, addr = self.server_socket.accept()
                conn.settimeout(30)  # Increased timeout
                print(f"Connection received from {addr}")
                
                data = recv_msg(conn)
                if not isinstance(data, dict) or data.get("type") != "name":
                    send_msg(conn, {"type": "error", "msg": "Invalid connection request"})
                    conn.close()
                    continue
                
                name = data.get("name", f"Player {connected_players + 1}")
                
                # Handle reconnection
                if name in self.name_to_index:
                    index = self.name_to_index[name]
                    if self.reconnect_deadlines[index] and time.time() > self.reconnect_deadlines[index]:
                        send_msg(conn, {"type": "error", "msg": "Reconnection window expired. Game already concluded."})
                        conn.close()
                        continue
                    
                    print(f"{name} is reconnecting.")
                    self.clients[index] = conn
                    self.heartbeat_times[index] = time.time()
                    self.reconnect_deadlines[index] = None
                    
                    # Send comprehensive reconnection data
                    reconnect_data = {
                        "type": "resume",
                        "player_index": index,
                        "stack": list(self.stacks[index]),
                        "round": self.current_round,
                        "opponent": self.client_names[1 - index]
                    }
                    send_msg(conn, reconnect_data)
                    continue
                
                # New player connection
                if connected_players < 2:
                    self.client_names[connected_players] = name
                    self.name_to_index[name] = connected_players
                    self.clients[connected_players] = conn
                    self.heartbeat_times[connected_players] = time.time()
                    
                    # Send initial connection confirmation
                    init_data = {
                        "type": "connected",
                        "player_index": connected_players,
                        "name": name
                    }
                    send_msg(conn, init_data)
                    
                    print(f"{name} connected as Player {connected_players}.")
                    connected_players += 1
                else:
                    send_msg(conn, {"type": "error", "msg": "Server is full. Only 2 players allowed."})
                    conn.close()
                    
            except Exception as e:
                print(f"Error accepting client: {e}")
                continue

        threading.Thread(target=self.reject_extra_clients, daemon=True).start()
        threading.Thread(target=self.heartbeat_monitor, daemon=True).start()
            
    def reject_extra_clients(self):
        while not self.disconnected.is_set():
            try:
                conn, addr = self.server_socket.accept()
                print(f"Extra connection attempt from {addr}. Rejecting...")
                send_msg(conn, {"type": "error", "msg": "Server is full. Only 2 players allowed."})
                time.sleep(1)
                conn.close()
            except:
                break                   

    def heartbeat_monitor(self):
        while not self.disconnected.is_set():
            now = time.time()
            for i in range(2):
                if (self.clients[i] and 
                    (now - self.heartbeat_times[i] > self.heartbeat_interval) and
                    not self.reconnect_deadlines[i]):  # Don't timeout if already disconnected
                    player_name = self.client_names[i] if self.client_names[i] else f"Player {i}"
                    print(f"Heartbeat timeout for {player_name}")
                    self.handle_disconnect(i)
            time.sleep(5)

    def start_client_threads(self):
        for i in range(2):
            thread = threading.Thread(target=self.handle_client_ready, args=(i,))
            thread.start()
            self.client_threads.append(thread)

    def handle_client_ready(self, i):
        while not self.disconnected.is_set():
            try:
                if not self.clients[i]:  # Client disconnected
                    break
                    
                data = recv_msg(self.clients[i])
                if data == "ready":
                    if self.clients[i]:  # Double-check client is still connected
                        self.ready_flags[i].set()
                elif data == "heartbeat":
                    self.heartbeat_times[i] = time.time()
                elif data == "shutdown":
                    player_name = self.client_names[i] if self.client_names[i] else f"Player {i}"
                    print(f"{player_name} requested shutdown.")
                    self.send_all({
                        "type": "game_end",
                        "message": f"{player_name} has quit the game. Server shutting down."
                    })
                    self.disconnected.set()
                    break
                elif data is None:
                    self.handle_disconnect(i)
                    break
            except Exception as e:
                player_name = self.client_names[i] if self.client_names[i] else f"Player {i}"
                print(f"Error with {player_name}: {e}")
                self.handle_disconnect(i)
                break
            
    def handle_disconnect(self, i):
        if self.clients[i]:  # Only handle if not already disconnected
            player_name = self.client_names[i] if self.client_names[i] else f"Player {i}"
            print(f"{player_name} disconnected.")
            self.reconnect_deadlines[i] = time.time() + self.reconnect_timeout
            try:
                self.clients[i].close()
            except:
                pass
            self.clients[i] = None
            self.ready_flags[i].clear()  # Clear ready flag on disconnect

    def send_all(self, data):
        disconnected_clients = []
        for i, conn in enumerate(self.clients):
            if conn:
                if not send_msg(conn, data):
                    disconnected_clients.append(i)
        
        # Handle clients that failed to receive message
        for i in disconnected_clients:
            self.handle_disconnect(i)

    def refill_stack_if_needed(self, i):
        if not self.stacks[i] and self.winning_piles[i]:
            self.stacks[i] = self.winning_piles[i]
            random.shuffle(self.stacks[i])
            self.winning_piles[i] = deque()

    def check_game_end(self):
        for i in range(2):
            if len(self.stacks[i]) == 0 and len(self.winning_piles[i]) == 0:
                winner_idx = 1 - i
                self.send_all({
                    "type": "game_end",
                    "winner": self.client_names[winner_idx],
                    "loser": self.client_names[i],
                    "message": f"{self.client_names[i]} is out of cards. {self.client_names[winner_idx]} wins!"
                })
                return True
        return False

    def game_loop(self):
        self.game_started = True
        
        while not self.disconnected.is_set():
            if self.check_game_end():
                break

            # Check if both players are connected
            if not all(self.clients):
                connected_player = 0 if self.clients[0] else 1
                if self.clients[connected_player]:
                    self.send_all({
                        "type": "game_end",
                        "message": f"Opponent disconnected. {self.client_names[connected_player]} wins by default!"
                    })
                self.disconnected.set()
                return

            # Refill stacks if needed
            for i in range(2):
                self.refill_stack_if_needed(i)

            print(f"Round {self.current_round + 1}: Waiting for both players to be ready...")
            self.current_round += 1

            # Wait for both players to be ready with timeout
            ready1 = self.ready_flags[0].wait(timeout=90)  # Increased timeout
            ready2 = self.ready_flags[1].wait(timeout=90)

            if not ready1 or not ready2:
                timeout_msg = "Timeout: One or both players did not respond in time. Game over."
                self.send_all({"type": "game_end", "message": timeout_msg})
                self.disconnected.set()
                return

            # Clear ready flags
            self.ready_flags[0].clear()
            self.ready_flags[1].clear()

            # Play the round
            try:
                cards_in_play = [self.stacks[i].popleft() for i in range(2)]
                pot = list(cards_in_play)

                # Handle war (tie) situations
                war_count = 0
                while card_value(cards_in_play[0]) == card_value(cards_in_play[1]):
                    war_count += 1
                    print(f"WAR! Round {war_count}")
                    
                    for i in range(2):
                        self.refill_stack_if_needed(i)
                        if len(self.stacks[i]) < 2:
                            winner_idx = 1 - i
                            self.send_all({
                                "type": "game_end",
                                "winner": self.client_names[winner_idx],
                                "loser": self.client_names[i],
                                "message": f"WAR! {self.client_names[i]} cannot continue. {self.client_names[winner_idx]} wins!"
                            })
                            return
                        
                        # Add face-down card and face-up card
                        pot.append(self.stacks[i].popleft())  # Face down
                        cards_in_play[i] = self.stacks[i].popleft()  # Face up
                        pot.append(cards_in_play[i])

                # Determine winner
                winner_idx = 0 if card_value(cards_in_play[0]) > card_value(cards_in_play[1]) else 1
                self.winning_piles[winner_idx].extend(pot)
                
                # Send round result to all players
                round_result = {
                    "type": "round_result",
                    "cards": cards_in_play,
                    "winner_index": winner_idx,
                    "winner_name": self.client_names[winner_idx],
                    "pot_size": len(pot),
                    "war_count": war_count
                }
                self.send_all(round_result)

            except Exception as e:
                print(f"Error during game round: {e}")
                self.send_all({"type": "game_end", "message": "Game error occurred. Ending game."})
                break

        self.cleanup()

    def cleanup(self):
        print("Cleaning up connections.")
        self.disconnected.set()
        
        # Close all client connections
        for conn in self.clients:
            if conn:
                try:
                    conn.close()
                except:
                    pass
        
        # Close server socket
        try:
            self.server_socket.close()
        except:
            pass
        
        # Close UDP socket
        if self.udp_socket:
            try:
                self.udp_socket.close()
            except:
                pass

    def run(self):
        try:
            self.start_udp_broadcast()
            self.wait_for_clients()

            # Deal cards
            deck = create_deck()
            random.shuffle(deck)
            self.stacks[0] = deque(deck[:26])
            self.stacks[1] = deque(deck[26:])

            # Send initial game data to both players
            for i in range(2):
                if self.clients[i]:
                    game_start_data = {
                        "type": "game_start",
                        "stack": list(self.stacks[i]),
                        "opponent": self.client_names[1 - i]
                    }
                    send_msg(self.clients[i], game_start_data)

            self.start_client_threads()
            self.game_loop()
            
        except Exception as e:
            print(f"Server error: {e}")
        finally:
            self.cleanup()

if __name__ == '__main__':
    server = WarGameServer()
    server.run()