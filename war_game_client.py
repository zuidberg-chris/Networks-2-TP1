import socket
import pickle
import struct
import threading
import time

def discover_server():
    """Discover server on local network with fallback to localhost"""
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    udp_socket.bind(('', 54545))
    udp_socket.settimeout(15)  # Increased timeout
    print("Searching for server on local network...")

    try:
        message, _ = udp_socket.recvfrom(1024)
        host, port = message.decode().split(":")
        print(f"Found server at {host}:{port}")
        return host, int(port)
    except socket.timeout:
        print("Server discovery timed out. Trying localhost...")
        return "127.0.0.1", 5555  # Fallback to localhost
    finally:
        udp_socket.close()

def send_msg(sock, data):
    """Send message with error handling"""
    try:
        msg = pickle.dumps(data)
        msg = struct.pack('>I', len(msg)) + msg
        sock.sendall(msg)
        return True
    except Exception as e:
        print(f"Send error: {e}")
        return False

def recv_msg(sock):
    """Receive message with error handling"""
    try:
        raw_msglen = recvall(sock, 4)
        if not raw_msglen:
            return None
        msglen = struct.unpack('>I', raw_msglen)[0]
        return pickle.loads(recvall(sock, msglen))
    except Exception as e:
        print(f"Receive error: {e}")
        return None

def recvall(sock, n):
    """Receive exactly n bytes"""
    data = bytearray()
    while len(data) < n:
        try:
            packet = sock.recv(n - len(data))
            if not packet:
                return None
            data.extend(packet)
        except socket.timeout:
            print("Server timeout. No data received.")
            return None
        except Exception as e:
            print(f"Socket recv error: {e}")
            return None
    return data

def heartbeat_loop(sock, stop_event):
    """Send periodic heartbeats to server"""
    while not stop_event.is_set():
        if not send_msg(sock, "heartbeat"):
            break
        time.sleep(15)  # Adjusted to match server expectations

def main():
    # Discover and connect to server
    try:
        SERVER_HOST, SERVER_PORT = discover_server()
    except SystemExit:
        return

    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client_socket.settimeout(30)  # Increased timeout

    try:
        client_socket.connect((SERVER_HOST, SERVER_PORT))
        print(f"Connected to server at {SERVER_HOST}:{SERVER_PORT}")
    except Exception as e:
        print(f"Could not connect to server: {e}")
        return

    # Get player name
    name = input("Enter your player name: ").strip()
    if not name:
        name = f"Player_{int(time.time()) % 1000}"  # Generate unique name if empty

    # Send name to server
    try:
        if not send_msg(client_socket, {"type": "name", "name": name}):
            print("Failed to send name to server")
            client_socket.close()
            return

        response = recv_msg(client_socket)
        if not response:
            print("No response from server")
            client_socket.close()
            return

        # Handle server responses
        if isinstance(response, dict):
            if response.get("type") == "error":
                print(f"Server error: {response.get('msg')}")
                client_socket.close()
                return
            elif response.get("type") == "connected":
                player_index = response.get("player_index", 0)
                print(f"Connected as {response.get('name')} (Player {player_index})")
                print("Waiting for other player...")
            elif response.get("type") == "resume":
                player_index = response.get("player_index", 0)
                stack = response.get("stack", [])
                round_num = response.get("round", 0)
                opponent = response.get("opponent", "Unknown")
                print(f"Reconnected as Player {player_index}")
                print(f"Game resumed at round {round_num}, opponent: {opponent}")
                print(f"Your stack has {len(stack)} cards")
        else:
            # Legacy response format
            player_index = 0  # Default assumption for backwards compatibility
            print("Connected to server. Waiting for game to start...")

    except Exception as e:
        print(f"Connection error: {e}")
        client_socket.close()
        return

    # Start heartbeat thread
    stop_event = threading.Event()
    heartbeat_thread = threading.Thread(target=heartbeat_loop, args=(client_socket, stop_event), daemon=True)
    heartbeat_thread.start()

    game_over = False
    stack_size = 0

    print("\nGame starting! Press Enter to play your next card, or type 'q' to quit.")

    while not game_over:
        # Get user input
        try:
            cmd = input("\nPress Enter to play your next card (or 'q' to quit): ").strip()
            if cmd.lower() == 'q':
                if send_msg(client_socket, "shutdown"):
                    print("Requested server shutdown.")
                break
        except (EOFError, KeyboardInterrupt):
            print("\nQuitting game...")
            send_msg(client_socket, "shutdown")
            break

        # Send ready signal
        if not send_msg(client_socket, "ready"):
            print("Failed to send ready signal")
            break

        print("Waiting for the other player...")

        # Wait for game messages
        while True:
            msg = recv_msg(client_socket)
            if msg is None:
                print("Disconnected from server.")
                game_over = True
                break

            # Handle different message types
            if isinstance(msg, dict):
                msg_type = msg.get("type", "")
                
                if msg_type == "game_start":
                    stack = msg.get("stack", [])
                    opponent = msg.get("opponent", "Unknown")
                    stack_size = len(stack)
                    print(f"Game started! Opponent: {opponent}")
                    print(f"You have {stack_size} cards in your stack")
                    break
                    
                elif msg_type == "round_result":
                    cards = msg.get("cards", [])
                    winner_index = msg.get("winner_index", 0)
                    winner_name = msg.get("winner_name", "Unknown")
                    pot_size = msg.get("pot_size", 2)
                    war_count = msg.get("war_count", 0)
                    
                    if len(cards) >= 2:
                        my_card = cards[player_index] if player_index < len(cards) else cards[0]
                        opp_card = cards[1 - player_index] if (1 - player_index) < len(cards) else cards[1]
                        
                        war_text = f" (after {war_count} war{'s' if war_count != 1 else ''})" if war_count > 0 else ""
                        
                        if winner_index == player_index:
                            print(f"You play {my_card}, opponent plays {opp_card}. You WIN this round{war_text}! (+{pot_size} cards)")
                        else:
                            print(f"You play {my_card}, opponent plays {opp_card}. Opponent wins this round{war_text}. (-{pot_size} cards)")
                    break
                    
                elif msg_type == "game_end":
                    message = msg.get("message", "Game ended")
                    winner = msg.get("winner", "")
                    loser = msg.get("loser", "")
                    
                    print(f"\n{message}")
                    if winner and loser:
                        if winner == name:
                            print("Congratulations! You won the game!")
                        else:
                            print(f"Game over. {winner} won.")
                    
                    game_over = True
                    break
                    
                else:
                    # Handle any other dict messages
                    if "message" in msg:
                        print(msg["message"])
                        if any(end_phrase in msg["message"].lower() for end_phrase in 
                               ["game over", "wins!", "lost the game", "disconnected", "timeout", "shutting down", "quit"]):
                            game_over = True
                            break
                    
            elif isinstance(msg, str):
                # Legacy string messages
                print(msg)
                if any(end_phrase in msg.lower() for end_phrase in 
                       ["lost the game", "wins!", "disconnected", "timeout", "server shutting down", "has quit the game"]):
                    game_over = True
                    break
                    
            else:
                # Legacy tuple format (cards, winner)
                try:
                    if isinstance(msg, (list, tuple)) and len(msg) == 2:
                        cards, winner = msg
                        if len(cards) >= 2:
                            my_card = cards[player_index] if player_index < len(cards) else cards[0]
                            opp_card = cards[1 - player_index] if (1 - player_index) < len(cards) else cards[1]
                            
                            if winner == player_index:
                                print(f"You play {my_card}, opponent plays {opp_card}. You WIN this round!")
                            else:
                                print(f"You play {my_card}, opponent plays {opp_card}. Opponent wins this round.")
                        break
                except Exception as e:
                    print(f"Error processing message: {e}")
                    continue

    # Cleanup
    stop_event.set()
    try:
        client_socket.close()
    except:
        pass
    print("Connection closed. Thanks for playing!")

if __name__ == "__main__":
    main()