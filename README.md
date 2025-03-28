![swaypadm2](https://github.com/user-attachments/assets/01bdf424-7dce-4a99-9631-de3b7e87313b)

<br>

# 🌊 Sway-pad

A lightweight, terminal-based text editor for Linux written in Python with TOML configuration. Fast, customizable, and optimized for Sway/i3wm environments.

<br>

<br>

Advanced text editor with syntax highlighting and multithreading support.

## License
This program is licensed under the **GNU General Public License v3.0 (GPL-3.0)**.  
See [LICENSE](LICENSE) for details.

<br>

## Installation

<br>

### Linux

```bash
pip install sway-pad
```

<br>

### FreeBSD

```sh
pkg install sway-pad
```

---

<br>

<br>

# 🌊 Sway-pad

### **Project Description**

**Sway Notepad** is a **console-based text editor for Linux**, written in Python using the **curses** library. It supports **syntax highlighting, customizable hotkeys, and color schemes**, all loaded from a **config.toml** file.

---

## 🔹 **Key Features**:
- **Text editing** with line numbers and cursor support.
- **Syntax highlighting** for Python and JavaScript (extensible).
- **Customizable hotkeys** (Ctrl+S — save, Ctrl+Q — quit, etc.).
- **Color schemes** from a TOML config (background, text, syntax colors).
- **Basic file operations**: open, save, copy, paste.
- **Vertical and horizontal scrolling**.
- **Keyboard input handling** with navigation support.

---

## ⚙ **Technical Details**
- Uses **curses** for rendering the interface.
- Loads configuration from **config.toml**.
- Supports **various encodings** and formats.
- Easily allows adding **new languages** for syntax highlighting.
- Minimal system requirements — works even in **low-resource terminal environments**.

---

## 🔧 **Sample Configuration (config.toml)**
```toml
[keybindings]
save_file = "ctrl+s"
quit = "ctrl+q"

[colors]
background = "black"
foreground = "white"
keyword_color = "cyan"
string_color = "green"
comment_color = "yellow"

[syntax_highlighting]
python = [
    { pattern = "def\\s+\\w+", color = "cyan" },
    { pattern = "\\\".*?\\\"", color = "green" },
    { pattern = "#.*", color = "yellow" }
]
```

---

## 🛠 **How to Run?**
```bash
python3 sway.py
```

If you need to change settings, edit the **config.toml** file.

---

## 🌟 **Why Try Sway-pad?**

✅ Lightweight and fast  

✅ Works in any **Linux terminal**  

✅ Easy to configure  

✅ Fully **Open Source**  

Everyone interested is welcome to collaborate 🚀
