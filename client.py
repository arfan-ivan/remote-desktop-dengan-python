import socket
import struct
import cv2
import numpy as np
import pygame
import time
import threading
import queue

# Konfigurasi client
SERVER_IP = "172.16.1.4"  # Ganti dengan IP server
PORT = 9999

WINDOW_WIDTH = 1024  
WINDOW_HEIGHT = 768  
FULLSCREEN = False   
ADAPTIVE_QUALITY = True  

connected = False
running = True
server_width = 0
server_height = 0

frame_queue = queue.Queue(maxsize=3)
message_queue = queue.Queue()
status_message = "Menghubungkan ke server..."

frame_times = []
bandwidth_usage = []
current_fps = 0
current_bandwidth = 0

def send_command(sock, command):
    try:
        message = command + "\n"
        sock.sendall(message.encode())
    except:
        pass

def recv_all(sock, n):
    data = b""
    while len(data) < n:
        packet = sock.recv(min(4096, n - len(data)))
        if not packet:
            return None
        data += packet
    return data

def update_performance_metrics(frame_size):
    global frame_times, bandwidth_usage, current_fps, current_bandwidth
    
    current_time = time.time()
    frame_times.append(current_time)
    
    if len(frame_times) > 100:
        frame_times.pop(0)
    
    if len(frame_times) > 1:
        current_fps = 1.0 / (frame_times[-1] - frame_times[-2])
    
    bandwidth_usage.append((current_time, frame_size))
    
    while bandwidth_usage and current_time - bandwidth_usage[0][0] > 5.0:
        bandwidth_usage.pop(0)
    
    if len(bandwidth_usage) > 1:
        total_bytes = sum(size for _, size in bandwidth_usage)
        time_span = bandwidth_usage[-1][0] - bandwidth_usage[0][0]
        if time_span > 0:
            current_bandwidth = total_bytes / time_span / 1024  # KB/s

def network_thread():
    global connected, server_width, server_height, status_message, running
    
    status_message = f"Menghubungkan ke {SERVER_IP}:{PORT}..."
    
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    try:
        client_socket.settimeout(5.0)
        client_socket.connect((SERVER_IP, PORT))
        client_socket.settimeout(1.0)  
        
        config_data = b""
        while b"\n" not in config_data:
            chunk = client_socket.recv(1024)
            if not chunk:
                status_message = "Koneksi terputus saat menerima konfigurasi"
                client_socket.close()
                return
            config_data += chunk
        
        config_str = config_data.split(b"\n")[0].decode()
        if config_str.startswith("CONFIG "):
            parts = config_str.split()
            if len(parts) >= 3:
                server_width = int(parts[1])
                server_height = int(parts[2])
                print(f"Ukuran layar server: {server_width}x{server_height}")
        
        remaining_data = b"".join(config_data.split(b"\n")[1:])
        
        connected = True
        
        while connected and running:
            try:
                while not message_queue.empty():
                    msg = message_queue.get_nowait()
                    send_command(client_socket, msg)
                
                header_data = remaining_data if remaining_data else recv_all(client_socket, 16)
                remaining_data = b""
                
                if not header_data or len(header_data) < 16:
                    print("Koneksi terputus saat menerima header")
                    break
                    
                data_len, width, height = struct.unpack("QII", header_data[:16])
                
                image_data = recv_all(client_socket, data_len)
                if not image_data:
                    print("Koneksi terputus saat menerima data gambar")
                    break
                    
                update_performance_metrics(len(image_data))
                
                img_np = np.frombuffer(image_data, dtype=np.uint8)
                frame = cv2.imdecode(img_np, cv2.IMREAD_COLOR)
                
                if frame is not None:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    
                    try:
                        if frame_queue.full():
                            frame_queue.get_nowait()
                        frame_queue.put_nowait(frame)
                    except:
                        pass
            
            except socket.timeout:
                pass
            except Exception as e:
                status_message = f"Error jaringan: {str(e)}"
                print(f"Network error: {e}")
                break
        
    except socket.timeout:
        status_message = f"Timeout saat menghubungkan ke {SERVER_IP}:{PORT}"
    except ConnectionRefusedError:
        status_message = f"Koneksi ditolak oleh {SERVER_IP}:{PORT}"
    except Exception as e:
        status_message = f"Error: {str(e)}"
        print(f"Connection error: {e}")
    finally:
        connected = False
        try:
            client_socket.close()
        except:
            pass

def main():
    global WINDOW_WIDTH, WINDOW_HEIGHT, FULLSCREEN, running, connected, status_message
    
    pygame.init()
    pygame.display.set_caption("Remote Desktop Client")
    
    if FULLSCREEN:
        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        WINDOW_WIDTH, WINDOW_HEIGHT = screen.get_size()
    else:
        screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.RESIZABLE)
    
    font = pygame.font.Font(None, 24)
    
    network_thread_instance = threading.Thread(target=network_thread)
    network_thread_instance.daemon = True
    network_thread_instance.start()
    
    key_states = {}  
    
    clock = pygame.time.Clock()
    
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
                break
            
            if not connected:
                if event.type == pygame.VIDEORESIZE:
                    if not FULLSCREEN:
                        WINDOW_WIDTH, WINDOW_HEIGHT = event.size
                        screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.RESIZABLE)
                continue
            
            if event.type == pygame.VIDEORESIZE:
                if not FULLSCREEN:
                    WINDOW_WIDTH, WINDOW_HEIGHT = event.size
                    screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.RESIZABLE)
            
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_F11:
                FULLSCREEN = not FULLSCREEN
                if FULLSCREEN:
                    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
                    WINDOW_WIDTH, WINDOW_HEIGHT = screen.get_size()
                else:
                    screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.RESIZABLE)
            
            elif event.type == pygame.KEYDOWN:
                key = pygame.key.name(event.key)
                if key not in key_states:
                    key_states[key] = True
                    message_queue.put(f"KEY_DOWN {key}")
            
            elif event.type == pygame.KEYUP:
                key = pygame.key.name(event.key)
                if key in key_states:
                    del key_states[key]
                    message_queue.put(f"KEY_UP {key}")
            
            elif event.type == pygame.MOUSEMOTION:
                x, y = pygame.mouse.get_pos()
                message_queue.put(f"MOUSE_MOVE {x} {y} {WINDOW_WIDTH} {WINDOW_HEIGHT}")
            
            elif event.type == pygame.MOUSEBUTTONDOWN:
                button = event.button  
                if button in [4, 5]:
                    scroll_amount = 5 if button == 4 else -5
                    message_queue.put(f"MOUSE_SCROLL {scroll_amount}")
                else:
                    message_queue.put(f"MOUSE_CLICK {button}")
            
            elif event.type == pygame.MOUSEWHEEL:
                scroll_amount = event.y * 3
                message_queue.put(f"MOUSE_SCROLL {scroll_amount}")
        
        if connected:
            try:
                if not frame_queue.empty():
                    frame = frame_queue.get_nowait()
                    
                    if frame.shape[1] != WINDOW_WIDTH or frame.shape[0] != WINDOW_HEIGHT:
                        frame = cv2.resize(frame, (WINDOW_WIDTH, WINDOW_HEIGHT))
                    
                    pygame_frame = pygame.surfarray.make_surface(frame.swapaxes(0, 1))
                    screen.blit(pygame_frame, (0, 0))
                    
                    overlay = pygame.Surface((250, 80), pygame.SRCALPHA)
                    overlay.fill((0, 0, 0, 128))
                    screen.blit(overlay, (10, 10))
                    
                    fps_text = font.render(f"FPS: {current_fps:.1f}", True, (255, 255, 255))
                    screen.blit(fps_text, (20, 20))
                    
                    bw_text = font.render(f"Bandwidth: {current_bandwidth:.1f} KB/s", True, (255, 255, 255))
                    screen.blit(bw_text, (20, 45))
                    
                    res_text = font.render(f"Resolution: {server_width}x{server_height}", True, (255, 255, 255))
                    screen.blit(res_text, (20, 70))
            except Exception as e:
                print(f"Error rendering frame: {e}")
        else:
            screen.fill((0, 0, 0))
            text = font.render(status_message, True, (255, 255, 255))
            text_rect = text.get_rect(center=(WINDOW_WIDTH/2, WINDOW_HEIGHT/2))
            screen.blit(text, text_rect)
        
        pygame.display.flip()
        
        clock.tick(60)
    
    pygame.quit()

if __name__ == "__main__":
    main()