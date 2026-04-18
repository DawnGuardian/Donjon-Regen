"""Simple GUI for donjon-regen."""

import json
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

from generate import generate_dungeon


class DonjonRegenApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Donjon Regen")
        self.root.resizable(False, False)

        self.json_path = tk.StringVar()
        self.output_dir = tk.StringVar()

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # --- JSON file selection ---
        frame_json = tk.LabelFrame(self.root, text="Dungeon JSON", padx=8, pady=8)
        frame_json.grid(row=0, column=0, sticky="ew", **pad)
        frame_json.columnconfigure(0, weight=1)

        self.json_entry = tk.Entry(frame_json, textvariable=self.json_path, width=50)
        self.json_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        tk.Button(frame_json, text="Browse...", command=self._browse_json).grid(
            row=0, column=1
        )

        # --- Output directory selection ---
        frame_out = tk.LabelFrame(self.root, text="Output Directory", padx=8, pady=8)
        frame_out.grid(row=1, column=0, sticky="ew", **pad)
        frame_out.columnconfigure(0, weight=1)

        self.out_entry = tk.Entry(frame_out, textvariable=self.output_dir, width=50)
        self.out_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        tk.Button(frame_out, text="Browse...", command=self._browse_output).grid(
            row=0, column=1
        )

        # --- Generate button ---
        self.gen_button = tk.Button(
            self.root, text="Generate", command=self._on_generate, width=20
        )
        self.gen_button.grid(row=2, column=0, **pad)

        # --- Log area ---
        frame_log = tk.LabelFrame(self.root, text="Log", padx=8, pady=8)
        frame_log.grid(row=3, column=0, sticky="nsew", **pad)
        frame_log.columnconfigure(0, weight=1)
        frame_log.rowconfigure(0, weight=1)

        self.log_text = tk.Text(frame_log, height=10, width=60, state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")

        scrollbar = tk.Scrollbar(frame_log, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def _browse_json(self):
        path = filedialog.askopenfilename(
            title="Select Dungeon JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.json_path.set(path)
            # Default output dir to same folder as JSON
            if not self.output_dir.get():
                self.output_dir.set(str(Path(path).parent))

    def _browse_output(self):
        path = filedialog.askdirectory(title="Select Output Directory")
        if path:
            self.output_dir.set(path)

    def _log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_generate(self):
        json_file = self.json_path.get().strip()
        out_dir = self.output_dir.get().strip()

        if not json_file:
            messagebox.showerror("Error", "Please select a dungeon JSON file.")
            return
        if not Path(json_file).is_file():
            messagebox.showerror("Error", f"File not found:\n{json_file}")
            return
        if not out_dir:
            out_dir = str(Path(json_file).parent)
            self.output_dir.set(out_dir)

        # Clear log
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        self.gen_button.configure(state="disabled", text="Generating...")

        # Run generation in a background thread to keep the GUI responsive
        thread = threading.Thread(
            target=self._run_generation, args=(json_file, out_dir), daemon=True
        )
        thread.start()

    def _run_generation(self, json_file, out_dir):
        try:
            with open(json_file) as f:
                dungeon = json.load(f)

            def on_progress(msg):
                self.root.after(0, self._log, msg)

            generated = generate_dungeon(dungeon, out_dir, on_progress=on_progress)

            self.root.after(
                0,
                lambda: messagebox.showinfo(
                    "Complete",
                    f"Generated {len(generated)} files in:\n{out_dir}",
                ),
            )
        except Exception as e:
            self.root.after(
                0,
                lambda: messagebox.showerror("Error", f"Generation failed:\n{e}"),
            )
            self.root.after(0, self._log, f"ERROR: {e}")
        finally:
            self.root.after(
                0,
                lambda: self.gen_button.configure(state="normal", text="Generate"),
            )


def main():
    root = tk.Tk()
    DonjonRegenApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
