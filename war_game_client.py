import socket
import pickle
import struct

SERVER_HOST = 'localhost'
SERVER_PORT = 5555

def send_msg(sock, data):
    msg = pickle.dumps(data)
    msg = struct.pack('>I', len(msg)) + msg
    sock.sendall(msg)

def recv_msg(sock):
    raw_msglen = recvall(sock, 4)
    if not raw_msglen:
        return None
    msglen = struct.unpack('>I', raw_msglen)[0]
    return pickle.loads(recvall(sock, msglen))

def recvall(sock, n):
    data = bytearray()
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data.extend(packet)
    return data

client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client_socket.connect((SERVER_HOST, SERVER_PORT))

stack = recv_msg(client_socket)
print("Initial stack received. Waiting for game updates...")

game_over = False

while not game_over:  # OUTER loop
    input("Press Enter to play your next card...")
    send_msg(client_socket, "ready")
    print("Waiting for the other player...")

    while True:  # INNER loop
        msg = recv_msg(client_socket)
        if msg is None:
            print("Disconnected from server.")
            game_over = True
            break

        if isinstance(msg, str):
            if "lost the game" in msg or "wins!" in msg:
                print(msg)
                game_over = True
                break
            else:
                print(msg)
                continue
        else:
            cards, winner = msg
            print(f"Player 1 plays {cards[0]}, Player 2 plays {cards[1]}. Player {winner+1} wins the round.")
            break  # Proceed to next outer loop iteration

client_socket.close()
