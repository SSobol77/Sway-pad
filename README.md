![swaypadm2](https://github.com/user-attachments/assets/01bdf424-7dce-4a99-9631-de3b7e87313b)

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python Version](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python)](https://www.gnu.org/licenses/gpl-3.0)
![Linux](https://img.shields.io/badge/Linux-FCC624?logo=linux&logoColor=black)
![FreeBSD](https://img.shields.io/badge/FreeBSD-AB2B28?logo=freebsd&logoColor=white)
![Windows](https://img.shields.io/badge/Windows-0078D6?logo=windows&logoColor=white)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/SSobol77/sway-pad/pulls)



---

<br>

> [!IMPORTANT]
> **🚫 Ethical Restrictions**
> 
> My works cannot be used in:
> 
> - Military applications or systems  
> - Surveillance technologies  
> - Any activity violating human rights

<br>

---

<br>

<br>

<div align="center">
  <h1>🌊 Sway-pad</h1>
  <h3>Cross-Platform Terminal Text Editor</h3>
</div>

---

<br>

## 🚀 Features

| **Category**         | **Details**                                                                 |
|----------------------|-----------------------------------------------------------------------------|
| **Core Engine**      | ⚡ Multithreaded Architecture • 🕒 Low Latency (<5ms) • 📦 2MB Memory Footprint |
| **Syntax Support**   | 40+ Languages (Python, Rust, Go, etc.) • 🎨 Theme Engine • 🔍 Regex Parsing |
| **Workflow**         | 🖱️ i3wm Integration • 📋 X11 Clipboard (via xclip) • 💻 TMux Compatible • 🧩 Plugin System |
| **Customization**    | 🔧 TOML Configuration • ⌨️ Keybind Profiles • 🌓 Dark/Light Themes           |
| **Performance**      | 🚀 <0.1s Startup • 📈 100k LOC Handling • 🔄 Auto-Reload Changed Files       |

<br>

---

<br>

## ⚡ Quick Start

### Installation  
```bash
# Linux (PyPI) - Required First Step:
sudo apt-get install xclip || sudo apt-get install xsel  # Must choose one
pip install sway-pad --user

# FreeBSD
sudo pkg install xclip sway-pad

# Development Build
git clone https://github.com/SSobol77/Sway-pad.git && cd Sway-pad
python3 -m pip install -e .
```

### Basic Usage  
```bash
# Open single file
sway-pad example.py

# Project mode (multi-tab)
sway-pad src/ tests/ config.toml
```

> [!Warning]
> ⚠️ **System Requirements:**  
> **Linux**  
> Editor **requires** either `xclip` or `xsel`  
> 
> **FreeBSD**  
> Editor **requires xclip only** (xsel removed from repositories)  
> 
> **All Systems:**  
> • Install required clipboard utility before first use  
> • Restart terminal after installation  

# Custom keybindings
```bash
sway-pad --config ~/.config/swaypad/keybinds.toml
```

---

<br>

## 🛠 Configuration

### `~/.config/swaypad/config.toml`
```toml
[editor]
theme = "nord"
font_family = "Fira Code"
tab_size = 4
auto_indent = true

[keybindings]
save = "ctrl+s"
quit = "ctrl+shift+q"
split_pane = "ctrl+alt+enter"

[plugins]
lsp_enabled = true
git_diff = { enabled = true, hotkey = "f5" }
```

<br>

---

<br>

## 📚 Supported Formats (40+)

| Language       | Extensions                                | Icon |
|----------------|-------------------------------------------|------|
| Python         | `.py`                                    | 🐍   |
| JavaScript     | `.js .mjs .cjs .jsx`                     | 🌐   |
| TypeScript     | `.ts .tsx`                               | 📘   |
| Java           | `.java`                                  | ☕   |
| C/C++          | `.c .h .cpp .hpp`                        | 🖥️  |
| Rust           | `.rs`                                    | 🦀   |
| Go             | `.go`                                    | 🐹   |
| Ruby           | `.rb .erb .rake`                         | 💎   |
| PHP            | `.php .phtml .php3 .php4 .php5`          | 🐘   |
| Swift          | `.swift`                                 | 🕊️  |
| Kotlin         | `.kt .kts`                               | 🔶   |
| SQL            | `.sql`                                   | 🗃️  |
| YAML           | `.yaml .yml`                             | ⚙️  |
| TOML           | `.toml .tml`                             | 🛠️  |
| JSON           | `.json`                                  | 📦  |
| XML            | `.xml`                                   | 📄  |
| HTML           | `.html .htm`                             | 🌍  |
| CSS            | `.css`                                   | 🎨  |
| Markdown       | `.md`                                    | 📝  |
| Shell          | `.sh .bash .zsh`                         | 🐚  |
| PowerShell     | `.ps1`                                   | ⚡  |
| Docker         | `Dockerfile`                             | 🐳  |
| Terraform      | `.tf`                                    | ☁️  |
| Git            | `.gitignore .gitconfig`                  | 🔀  |
| Lua            | `.lua`                                   | 🌙  |
| Perl           | `.pl .pm`                                | 🐪  |
| R              | `.r .R`                                  | 📊  |
| Julia          | `.jl`                                    | ◰   |
| Dart           | `.dart`                                  | 🎯  |
| Scala          | `.scala`                                 | 🌀  |
| Fortran        | `.f .F .f90 .F90 .for`                   | 🧮  |
| Makefile       | `Makefile`                               | 🛠️  |
| INI            | `.ini`                                   | ⚙️  |
| CSV            | `.csv`                                   | 📊  |
| Diff           | `.diff .patch`                           | 🔄  |
| GraphQL        | `.graphql`                               | 📡  |
| Jupyter        | `.ipynb`                                 | 📓  |

<br>

---

<br>

## 🏗 Architecture

```bash
Sway-pad/
├── core/               # Editor Engine
│   ├── renderer/       # Curses-based UI
│   ├── syntax/         # Language Parsers
│   └── plugins/        # LSP/Git Integration
├── themes/             # Color Schemes
│   ├── nord.toml
│   └── solarized_dark.toml
├── docs/               # Configuration Guides
├── tests/              # Benchmark Suite
└── swaypad.py          # CLI Entry Point
```

---

<br>

## 🧪 Development

### Testing Matrix
```bash
# Run Core Tests
pytest tests/core --cov --cov-report=html

# Performance Benchmark
python3 -m tests.benchmarks scroll_through_10k_lines

# Linting
flake8 . --count --max-complexity=10 --statistics
```

| **Metric**           | **Result**         |
|----------------------|--------------------|
| Test Coverage        | 92% Core Modules   |
| Max File Size        | 2GB (Compressed)   |
| Concurrent Sessions  | 8+ Tabs Stable     |

---

<br>

## 🤝 Contributing

1. Fork & Clone Repository
2. Create Feature Branch: `git checkout -b feat/your-feature`
3. Commit Changes: `git commit -m 'feat: add your feature'`
4. Push to Branch: `git push origin feat/your-feature`
5. Open Pull Request

**Contribution Guidelines**:  
- Follow PEP8 Style Guide  
- Add Type Hints for New Code  
- Include Benchmark Results for Performance Changes

---

<br>

## 📜 License

**GNU General Public License v3.0**  
Commercial use requires explicit permission.  
Full text available in [LICENSE](LICENSE).

---

<br>

## 📬 Contact

**Sergey Sobolewski**  

[![Email](https://img.shields.io/badge/Email-s.sobolewski@hotmail.com-blue?logo=protonmail)](mailto:s.sobolewski@hotmail.com)  

[![GitHub](https://img.shields.io/badge/GitHub-SSobol77-black?logo=github)](https://github.com/SSobol77)  

[![Website](https://img.shields.io/badge/Website-Cartesian_School-orange?logo=internet-explorer)](https://cartesianschool.com)

[![Discussions](https://img.shields.io/badge/Community-Discussions-blue?logo=github)](https://github.com/SSobol77/sway-pad/discussions)

[![Issues](https://img.shields.io/badge/Report-Bugs-red?logo=github)](https://github.com/SSobol77/Sway-pad/issues)

<br>
