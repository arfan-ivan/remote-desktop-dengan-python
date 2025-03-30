import socket
import struct
import pyautogui
from pynput.mouse import Controller as MouseController
from pynput.mouse import Button as MouseButton
from pynput.keyboard import Controller as KeyboardController, Key
import io
import time
import threading
import queue
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('remote_desktop_server.log')
    ]
)

HOST = '0.0.0.0'
PORT = 9999
QUALITY = 60       
FPS_LIMIT = 20     
SCREEN_SCALE = 0.8 
MAX_SEND_RETRIES = 3 
SEND_TIMEOUT = 3.0   
RECV_TIMEOUT = 0.5    

SPECIAL_KEYS = {
    "return": Key.enter,
    "space": Key.space,
    "backspace": Key.backspace,
    "tab": Key.tab,
    "escape": Key.esc,
    "delete": Key.delete,
    "up": Key.up,
    "down": Key.down,
    "left": Key.left,
    "right": Key.right,
    "home": Key.home,
    "end": Key.end,
    "page_up": Key.page_up,
    "page_down": Key.page_down,
    "f1": Key.f1, "f2": Key.f2, "f3": Key.f3, "f4": Key.f4,
    "f5": Key.f5, "f6": Key.f6, "f7": Key.f7, "f8": Key.f8,
    "f9": Key.f9, "f10": Key.f10, "f11": Key.f11, "f12": Key.f12,
    "ctrl": Key.ctrl, "alt": Key.alt, "shift": Key.shift
}

try:
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1048576)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1048576)
    server_socket.bind((HOST, PORT))
    server_socket.listen(1)
    logging.info(f"Server berjalan di {HOST}:{PORT}")
    logging.info("Menunggu koneksi dari client...")
except OSError as e:
    logging.error(f"Gagal membuat socket server: {e}")
    sys.exit(1)

command_queue = queue.Queue()
screenshot_queue = queue.Queue(maxsize=2)  

stop_event = threading.Event()

def safe_send(conn, data, retries=MAX_SEND_RETRIES):
    conn.settimeout(SEND_TIMEOUT)
    for attempt in range(retries):
        try:
            conn.sendall(data)
            return True
        except (socket.timeout, ConnectionResetError, BrokenPipeError) as e:
            if attempt < retries - 1:
                logging.warning(f"Pengiriman gagal (percobaan {attempt+1}/{retries}): {e}")
                time.sleep(0.2) 
            else:
                logging.error(f"Semua percobaan pengiriman gagal: {e}")
                return False
    return False

def screenshot_worker():
    screen_width, screen_height = pyautogui.size()
    target_width = int(screen_width * SCREEN_SCALE)
    target_height = int(screen_height * SCREEN_SCALE)
    
    last_capture_time = 0
    min_interval = 1.0 / FPS_LIMIT  

    logging.info(f"Screenshot worker dimulai: {target_width}x{target_height} @ {FPS_LIMIT} FPS")

    while not stop_event.is_set():
        current_time = time.time()
        if current_time - last_capture_time >= min_interval:
            try:
                screenshot = pyautogui.screenshot()
                
                if SCREEN_SCALE != 1.0:
                    screenshot = screenshot.resize((target_width, target_height))
                
                img_bytes = io.BytesIO()
                screenshot.save(img_bytes, format="JPEG", quality=QUALITY)
                img_data = img_bytes.getvalue()
                
                try:
                    if not screenshot_queue.full():
                        screenshot_queue.put_nowait((img_data, target_width, target_height))
                    else:
                        try:
                            screenshot_queue.get_nowait() 
                            screenshot_queue.put_nowait((img_data, target_width, target_height))
                        except queue.Empty:
                            pass
                except Exception as e:
                    logging.error(f"Error pada antrian screenshot: {e}")
                    
                last_capture_time = current_time
            except Exception as e:
                logging.error(f"Error saat mengambil screenshot: {e}")
        
        time.sleep(0.005)

def handle_client(conn, addr):
    logging.info(f"Koneksi dari {addr}")
    
    mouse = MouseController()
    keyboard = KeyboardController()
    
    screen_width, screen_height = pyautogui.size()
    logging.info(f"Ukuran layar: {screen_width}x{screen_height}")
    
    config_data = f"CONFIG {screen_width} {screen_height}\n"
    if not safe_send(conn, config_data.encode()):
        logging.error("Gagal mengirim konfigurasi ke client")
        return
    
    command_buffer = ""
    
    conn.settimeout(RECV_TIMEOUT)
    
    key_states = {}
    
    last_frame_time = 0
    min_frame_interval = 1.0 / FPS_LIMIT
    frame_counter = 0
    last_fps_report = time.time()
    
    last_activity_time = time.time()
    connection_active = True
    
    try:
        while connection_active and not stop_event.is_set():
            current_time = time.time()
            
            if current_time - last_frame_time >= min_frame_interval:
                try:
                    img_data, width, height = screenshot_queue.get_nowait()
                    
                    header = struct.pack("QII", len(img_data), width, height)
                    if not safe_send(conn, header):
                        logging.error("Gagal mengirim header gambar")
                        break
                    
                    if not safe_send(conn, img_data):
                        logging.error("Gagal mengirim data gambar")
                        break
                    
                    last_frame_time = current_time
                    frame_counter += 1
                    
                    if current_time - last_fps_report >= 5.0:
                        fps = frame_counter / (current_time - last_fps_report)
                        logging.info(f"Frame rate: {fps:.1f} FPS")
                        frame_counter = 0
                        last_fps_report = current_time
                    
                except queue.Empty:
                    pass
                except Exception as e:
                    logging.error(f"Error saat mengirim frame: {e}")
            
            try:
                recv_data = conn.recv(1024).decode()
                
                if not recv_data:
                    logging.info("Client terputus (tidak ada data)")
                    break
                
                last_activity_time = current_time
                
                command_buffer += recv_data
                
                while '\n' in command_buffer:
                    command, command_buffer = command_buffer.split('\n', 1)
                    command = command.strip()
                    
                    if not command:
                        continue
                    
                    parts = command.split(' ', 1)
                    cmd_type = parts[0]
                    args = parts[1] if len(parts) > 1 else ""
                    
                    if cmd_type == "MOUSE_MOVE":
                        try:
                            coords = args.split()
                            if len(coords) >= 2:
                                client_x, client_y = int(coords[0]), int(coords[1])
                                client_width = int(coords[2]) if len(coords) > 2 else screen_width
                                client_height = int(coords[3]) if len(coords) > 3 else screen_height
                                
                                target_x = int(client_x * screen_width / client_width)
                                target_y = int(client_y * screen_height / client_height)
                                mouse.position = (target_x, target_y)
                        except ValueError as e:
                            logging.warning(f"Kesalahan parsing koordinat mouse: {e}")

                    elif cmd_type == "MOUSE_CLICK":
                        try:
                            button_num = int(args)
                            
                            button_map = {
                                1: MouseButton.left,
                                2: MouseButton.middle,
                                3: MouseButton.right
                            }
                            
                            button = button_map.get(button_num, MouseButton.left)
                            
                            mouse.click(button)
                        except ValueError as e:
                            logging.warning(f"Error saat memproses klik mouse: {e}")

                    elif cmd_type == "MOUSE_SCROLL":
                        try:
                            scroll_amount = int(args)
                            pyautogui.scroll(scroll_amount) 
                        except ValueError:
                            direction = args
                            scroll_amount = 5 if direction == "UP" else -5
                            pyautogui.scroll(scroll_amount)

                    elif cmd_type == "KEY_DOWN":
                        key = args
                        if key in SPECIAL_KEYS:
                            k = SPECIAL_KEYS[key]
                            keyboard.press(k)
                            key_states[key] = k
                        elif len(key) == 1:  
                            keyboard.press(key)
                            key_states[key] = key
                    
                    elif cmd_type == "KEY_UP":
                        key = args
                        if key in key_states:
                            keyboard.release(key_states[key])
                            del key_states[key]
                        elif key in SPECIAL_KEYS:
                            keyboard.release(SPECIAL_KEYS[key])
                    
                    elif cmd_type == "KEY_PRESS":
                        key = args
                        if key in SPECIAL_KEYS:
                            keyboard.press(SPECIAL_KEYS[key])
                            keyboard.release(SPECIAL_KEYS[key])
                        elif len(key) == 1:  
                            keyboard.press(key)
                            keyboard.release(key)
                    
                    elif cmd_type == "PING":
                        if not safe_send(conn, "PONG\n".encode()):
                            logging.error("Gagal mengirim PONG")
                            connection_active = False
                        
            except socket.timeout:
                if current_time - last_activity_time > 30:  
                    logging.warning(f"Koneksi idle selama 30 detik, mengirim ping")
                    if not safe_send(conn, "PING\n".encode()):
                        logging.error("Koneksi terputus (ping gagal)")
                        connection_active = False
                    last_activity_time = current_time
            except ConnectionResetError:
                logging.error("Koneksi terputus (reset oleh peer)")
                connection_active = False
            except BrokenPipeError:
                logging.error("Koneksi terputus (pipe rusak)")
                connection_active = False
            except Exception as e:
                logging.error(f"Error saat menerima data: {e}")
                time.sleep(0.1)
                
            time.sleep(0.01)

    except Exception as e:
        logging.error(f"Error dalam handle_client: {e}")
        import traceback
        logging.error(traceback.format_exc())
    finally:
        for k in key_states.values():
            try:
                keyboard.release(k)
            except:
                pass
        
        try:
            conn.close()
        except:
            pass
        logging.info(f"Koneksi dari {addr} ditutup")

def main():
    try:
        screenshot_thread = threading.Thread(target=screenshot_worker, daemon=True)
        screenshot_thread.start()
        
        while not stop_event.is_set():
            try:
                
                server_socket.settimeout(1.0)
                try:
                    conn, addr = server_socket.accept()
                    
                    client_thread = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
                    client_thread.start()
                except socket.timeout:
                    continue
            except KeyboardInterrupt:
                logging.info("Server dihentikan oleh pengguna")
                stop_event.set()
                break
            except Exception as e:
                logging.error(f"Error saat menerima koneksi: {e}")
                time.sleep(1)  
            
    except KeyboardInterrupt:
        logging.info("Server dihentikan oleh pengguna")
    except Exception as e:
        logging.error(f"Error dalam main loop: {e}")
        import traceback
        logging.error(traceback.format_exc())
    finally:
        stop_event.set() 
        try:
            server_socket.close()
        except:
            pass
        logging.info("Server ditutup")

if __name__ == "__main__":
    main()