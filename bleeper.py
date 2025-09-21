import customtkinter as ctk
from PIL import Image
import threading
import serial
import pyaudio
import numpy as np
import time
import math
import platform

try:
    from pynput import keyboard
except ImportError:
    print("Error: pynput library not found.")
    print("Please install it by running: pip install pynput")
    exit()

# --- CONFIGURATION ---
SERIAL_PORT = "COM3"
BAUD_RATE = 115200

# --- UI & ANIMATION CONFIG ---
ANIMATION_DURATION_MS = 300  # Total duration for the slide animation
ANIMATION_FRAMES = 60  # Number of frames in the animation


class AudioRouterApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        # --- Theme & Color Palette ---
        ctk.set_appearance_mode("dark")
        self.COLOR_STATUS_NORMAL = ctk.ThemeManager.theme["CTkLabel"]["text_color"]
        self.COLOR_STATUS_SUCCESS = "#34eb77"
        self.COLOR_STATUS_WARNING = "#f2dd5c"
        self.COLOR_STATUS_ERROR = "#e84a4a"

        # --- Window Setup ---
        self.title("OpenBleep")
        self.geometry("450x700")
        self.minsize(450, 550)  # Adjusted minsize

        # Main layout: Center the main content frame
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)  # Spacer row
        self.grid_rowconfigure(1, weight=0)  # Main content here
        self.grid_rowconfigure(2, weight=1)  # Spacer row

        # --- App State ---
        self.is_running = False
        self.is_tone_active = False
        self.beep_amplitude = ctk.DoubleVar(value=0.25)
        self.phase = 0
        self.current_sample_rate = 44100
        self.p = pyaudio.PyAudio()
        self.stream = None
        self.ser = None
        self.master_listener_thread = None

        # --- Keybind Mode Attributes ---
        self.trigger_mode = "SERIAL"
        self.keybind = None
        self.keybind_listener = None
        self.keybind_capture_listener = None

        self.setup_ui()
        self.populate_devices()
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def setup_ui(self):
        """Creates a robust, card-based UI integrated into the grid layout."""
        # --- Main Content Frame ---
        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.grid(row=1, column=0, padx=20, pady=20, sticky="n")
        self.main_frame.grid_columnconfigure(0, weight=1)

        # --- Title ---
        title_label = ctk.CTkLabel(self.main_frame, text="We getting sweary up in here", font=ctk.CTkFont(size=24, weight="bold"))
        title_label.grid(row=0, column=0, padx=10, pady=(0, 20), sticky="ew")

        # --- Device Controls Frame ---
        device_frame = ctk.CTkFrame(self.main_frame)
        device_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 15))
        device_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(device_frame, text="AUDIO DEVICES", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="gray60").grid(row=0, column=0, padx=15, pady=(10, 5), sticky="w")
        self.input_device_combo = ctk.CTkComboBox(device_frame, values=["..."])
        self.input_device_combo.grid(row=1, column=0, padx=15, pady=5, sticky="ew")
        self.output_device_combo = ctk.CTkComboBox(device_frame, values=["..."])
        self.output_device_combo.grid(row=2, column=0, padx=15, pady=(5, 15), sticky="ew")

        # --- Beep Controls Frame ---
        beep_frame = ctk.CTkFrame(self.main_frame)
        beep_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 15))
        beep_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(beep_frame, text="BEEP VOLUME", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="gray60").grid(row=0, column=0, padx=15, pady=(10, 5), sticky="w")
        ctk.CTkSlider(beep_frame, from_=0.0, to=1.0, variable=self.beep_amplitude).grid(row=1, column=0, padx=15,
                                                                                        pady=(5, 15), sticky="ew")

        # --- Action Controls ---
        self.start_stop_button = ctk.CTkButton(self.main_frame, text="Start Routing", command=self.toggle_routing,
                                               font=ctk.CTkFont(size=14, weight="bold"))
        self.start_stop_button.grid(row=3, column=0, ipady=8, pady=(5, 10), sticky="ew")

        self.status_label = ctk.CTkLabel(self.main_frame, text="Status: Stopped")
        self.status_label.grid(row=4, column=0, pady=(0, 10), sticky="ew")

        # === RELIABLE KEYBIND FRAME SETUP ===
        # Create the frame and add it to the main content's grid.
        self.keybind_frame = ctk.CTkFrame(self.main_frame)
        self.keybind_frame.grid(row=5, column=0, sticky="nsew", pady=(15, 0))
        self.keybind_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self.keybind_frame, text="KEYBIND MODE", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="gray60").grid(row=0, column=0, padx=15, pady=(10, 5), sticky="w")
        self.keybind_display_label = ctk.CTkLabel(self.keybind_frame, text="Keybind: [Not Set]")
        self.keybind_display_label.grid(row=1, column=0, padx=15, pady=5, sticky="w")
        self.set_keybind_button = ctk.CTkButton(self.keybind_frame, text="Set Keybind", command=self.set_keybind_action)
        self.set_keybind_button.grid(row=2, column=0, padx=15, pady=(5, 15), sticky="ew")

        # Hide the frame by default using grid_remove(). This is robust.
        self.keybind_frame.grid_remove()

    def update_status(self, text, color=None):
        if color is None: color = self.COLOR_STATUS_NORMAL
        self.status_label.configure(text=text, text_color=color)

    def activate_keybind_mode_ui(self):
        """Reliably shows the keybind frame by adding it back to the grid."""
        self.update_status(f"Status: COM3 not found. Use Keybind Mode.", self.COLOR_STATUS_WARNING)
        # Simply grid the frame back into view. It will appear where it's supposed to.
        self.keybind_frame.grid()

    def toggle_routing(self):
        if self.is_running:  # --- STOPPING ---
            self.is_running = False
            self.start_stop_button.configure(text="Start Routing")
            self.update_status("Status: Stopped", self.COLOR_STATUS_NORMAL)

            # Reliably hide the keybind frame
            self.keybind_frame.grid_remove()

            if self.trigger_mode == "KEYBIND" and self.keybind_listener: self.keybind_listener.stop()
            if self.master_listener_thread and self.master_listener_thread.is_alive(): self.master_listener_thread.join(
                timeout=1)
            if self.ser and self.ser.is_open: self.ser.close()
            if self.stream and self.stream.is_active(): self.stream.stop_stream(); self.stream.close()
            self.stream = None
        else:  # --- STARTING ---
            try:
                input_idx = int(self.input_device_combo.get().split('[')[1].split(']')[0])
                output_idx = int(self.output_device_combo.get().split('[')[1].split(']')[0])
                for rate in [48000, 44100, 32000, 16000]:
                    if self.p.is_format_supported(rate, input_device=input_idx, input_channels=1,
                                                  input_format=pyaudio.paInt16) and \
                            self.p.is_format_supported(rate, output_device=output_idx, output_channels=1,
                                                       output_format=pyaudio.paInt16):
                        self.current_sample_rate = rate;
                        break
                else:
                    raise RuntimeError("No common sample rate found for devices.")

                self.stream = self.p.open(rate=self.current_sample_rate, format=pyaudio.paInt16, channels=1, input=True,
                                          output=True, input_device_index=input_idx, output_device_index=output_idx,
                                          frames_per_buffer=1024, stream_callback=self.audio_callback)
                self.stream.start_stream()
                self.is_running = True
                self.start_stop_button.configure(text="Stop Routing")

                self.master_listener_thread = threading.Thread(target=self.master_listener, daemon=True)
                self.master_listener_thread.start()
            except Exception as e:
                self.update_status(f"Error: {e}", self.COLOR_STATUS_ERROR);
                self.is_running = False

    # --- Other methods remain unchanged and correct ---
    def populate_devices(self):
        input_devices, output_devices = self.scan_audio_devices()
        if not input_devices and not output_devices: input_devices, output_devices = self.scan_audio_devices(
            use_preferred_api=False)
        if input_devices: self.input_device_combo.configure(values=input_devices); self.input_device_combo.set(
            input_devices[0])
        if output_devices:
            self.output_device_combo.configure(values=output_devices)
            self.output_device_combo.set(next((d for d in output_devices if 'CABLE Input' in d), output_devices[0]))

    def scan_audio_devices(self, use_preferred_api=True):
        input_devices, output_devices, unique_input_names, unique_output_names = [], [], set(), set()
        preferred_api_index = -1
        if platform.system() == "Windows" and use_preferred_api:
            for i in range(self.p.get_host_api_count()):
                if self.p.get_host_api_info_by_index(i)['name'] == 'Windows WASAPI': preferred_api_index = i; break
        for i in range(self.p.get_device_count()):
            dev_info = self.p.get_device_info_by_index(i)
            if preferred_api_index != -1 and dev_info['hostApi'] != preferred_api_index: continue
            if dev_info['maxInputChannels'] > 0 and dev_info['name'] not in unique_input_names:
                input_devices.append(f"[{i}] {dev_info['name']}");
                unique_input_names.add(dev_info['name'])
            if dev_info['maxOutputChannels'] > 0 and dev_info['name'] not in unique_output_names:
                output_devices.append(f"[{i}] {dev_info['name']}");
                unique_output_names.add(dev_info['name'])
        return input_devices, output_devices

    def audio_callback(self, in_data, frame_count, time_info, status):
        if self.is_tone_active:
            buffer_indices = np.arange(frame_count)
            phase_increment = 2.0 * math.pi * 1000.0 / self.current_sample_rate
            sine_wave = (np.sin(self.phase + buffer_indices * phase_increment) * self.beep_amplitude.get()).astype(
                np.float32)
            self.phase = (self.phase + frame_count * phase_increment) % (2.0 * math.pi)
            output_data_float = sine_wave
        else:
            output_data_float = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
        return ((output_data_float * 32767.0).astype(np.int16).tobytes(), pyaudio.paContinue)

    def master_listener(self):
        try:
            self.ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
            self.ser.dtr = False
            self.trigger_mode = "SERIAL"
            self.after(0, self.update_status, f"Status: Running (Listening on {SERIAL_PORT})",
                       self.COLOR_STATUS_SUCCESS)
            self.serial_loop()
        except serial.SerialException:
            self.trigger_mode = "KEYBIND"
            self.after(0, self.activate_keybind_mode_ui)
            self.keybind_loop()

    def serial_loop(self):
        while self.is_running:
            try:
                if not self.ser or not self.ser.is_open:
                    self.ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1);
                    self.ser.dtr = False
                    self.after(0, self.update_status, f"Status: Running (Listening on {SERIAL_PORT})",
                               self.COLOR_STATUS_SUCCESS)
                line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    if "[pressed]" in line:
                        self.is_tone_active = True
                    elif "[depressed]" in line:
                        self.is_tone_active = False
            except serial.SerialException:
                self.after(0, self.update_status, f"Status: Serial disconnected. Retrying...",
                           self.COLOR_STATUS_WARNING)
                if self.ser and self.ser.is_open: self.ser.close(); self.ser = None
                time.sleep(3)
            except Exception as e:
                print(f"Serial thread error: {e}"); break
            time.sleep(0.01)

    def keybind_loop(self):
        with keyboard.Listener(on_press=self.on_key_press, on_release=self.on_key_release) as listener:
            self.keybind_listener = listener;
            listener.join()
        print("Keybind listener stopped.")

    def on_key_press(self, key):
        if self.is_running and key == self.keybind: self.is_tone_active = True

    def on_key_release(self, key):
        if self.is_running and key == self.keybind: self.is_tone_active = False

    def set_keybind_action(self):
        if self.keybind_capture_listener and self.keybind_capture_listener.is_alive(): return
        self.set_keybind_button.configure(text="Press any key...", state="disabled")
        self.update_status("Status: Waiting for key input...", self.COLOR_STATUS_WARNING)
        self.keybind_capture_listener = keyboard.Listener(on_press=self.on_keybind_capture);
        self.keybind_capture_listener.start()

    def on_keybind_capture(self, key):
        self.keybind = key
        try:
            key_name = f"{key.char}"
        except AttributeError:
            key_name = f"{key}".replace("Key.", "")
        self.after(0, self.update_keybind_ui, key_name);
        return False

    def update_keybind_ui(self, key_name_str):
        self.keybind_display_label.configure(text=f"Keybind: [{key_name_str}]")
        self.set_keybind_button.configure(text="Set Keybind", state="normal")
        self.update_status("Status: Keybind set. Ready to start.", self.COLOR_STATUS_NORMAL)

    def on_closing(self):
        self.is_running = False
        time.sleep(0.1)
        if hasattr(self, 'keybind_listener') and self.keybind_listener: self.keybind_listener.stop()
        if self.stream: self.stream.stop_stream(); self.stream.close()
        if self.p: self.p.terminate()
        self.destroy()


if __name__ == "__main__":
    app = AudioRouterApp()
    app.mainloop()
