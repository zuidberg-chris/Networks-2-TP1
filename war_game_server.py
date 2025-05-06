import socket
import threading
import random
import pickle
import struct

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
    except Exception as e:
        print(f"Send failed: {e}")

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
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data.extend(packet)
    return data

class WarGameServer:
    def __init__(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.bind((HOST, PORT))
        self.server_socket.listen(2)
        print(f"Server listening on {HOST}:{PORT}")

        self.clients = [None, None]
        self.stacks = [[], []]
        self.winning_piles = [[], []]
        self.ready_flags = [threading.Event(), threading.Event()]
        self.client_threads = []
        self.disconnected = threading.Event()

    def refill_stack_if_needed(self, i):
        if not self.stacks[i]:
            self.stacks[i] = self.winning_piles[i]
            random.shuffle(self.stacks[i])
            self.winning_piles[i] = []

    def wait_for_clients(self):
        for i in range(2):
            conn, addr = self.server_socket.accept()
            print(f"Player {i+1} connected from {addr}")
            self.clients[i] = conn

    def start_client_threads(self):
        for i in range(2):
            thread = threading.Thread(target=self.handle_client_ready, args=(i,))
            thread.start()
            self.client_threads.append(thread)

    def handle_client_ready(self, i):
        while not self.disconnected.is_set():
            try:
                data = recv_msg(self.clients[i])
                if data == "ready":
                    self.ready_flags[i].set()
                elif data is None:
                    print(f"Player {i+1} disconnected.")
                    self.disconnected.set()
                    break
            except Exception as e:
                print(f"Error with player {i+1}: {e}")
                self.disconnected.set()
                break

    def send_all(self, data):
        for conn in self.clients:
            send_msg(conn, data)

    def game_loop(self):
        while not self.disconnected.is_set():
            if any(len(self.stacks[i]) == 0 and len(self.winning_piles[i]) == 0 for i in range(2)):
                loser = 0 if len(self.stacks[0]) == 0 and len(self.winning_piles[0]) == 0 else 1
                self.send_all(f"Player {loser+1} lost the game.")
                break

            for i in range(2):
                self.refill_stack_if_needed(i)

            # Wait for both players to be ready
            print("Waiting for both players to be ready...")
            self.ready_flags[0].wait()
            self.ready_flags[1].wait()
            self.ready_flags[0].clear()
            self.ready_flags[1].clear()

            cards_in_play = [self.stacks[i].pop(0) for i in range(2)]
            pot = cards_in_play.copy()

            while card_value(cards_in_play[0]) == card_value(cards_in_play[1]):
                for i in range(2):
                    self.refill_stack_if_needed(i)
                    if len(self.stacks[i]) < 2:
                        self.send_all(f"Player {i+1} cannot continue war. Player {2 - i} wins!")
                        return
                    pot.append(self.stacks[i].pop(0))  # face down
                    cards_in_play[i] = self.stacks[i].pop(0)  # face up
                    pot.append(cards_in_play[i])

            winner = 0 if card_value(cards_in_play[0]) > card_value(cards_in_play[1]) else 1
            self.winning_piles[winner].extend(pot)
            self.send_all((cards_in_play, winner))

        self.cleanup()

    def cleanup(self):
        print("Cleaning up connections.")
        for conn in self.clients:
            try:
                conn.close()
            except:
                pass

    def run(self):
        self.wait_for_clients()

        # Deal cards
        deck = create_deck()
        random.shuffle(deck)
        self.stacks[0], self.stacks[1] = deck[:26], deck[26:]

        for i in range(2):
            send_msg(self.clients[i], self.stacks[i])

        self.start_client_threads()
        self.game_loop()

if __name__ == '__main__':
    server = WarGameServer()
    server.run()
