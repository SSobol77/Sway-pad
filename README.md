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
  <h1>🌊Sway-pad</h1>
  <h3>Cross-Platform Terminal Code Editor for DevOps Engineers</h3>
</div>

<br>

---

<br>

### 🌊 **Sway-pad** — Lightweight Terminal Code Editor for DevOps Engineers and System Administrators

**Sway-pad** is a fast, minimalist, and highly configurable terminal-based code editor written in Python. Designed specifically for DevOps engineers and system administrators, it combines simplicity with powerful features — all in your terminal.

🧩 **Key Features:**

*  Terminal-first UI optimized for SSH and server environments
*  Instant startup and low memory usage — perfect for remote systems
*  Easy configuration via `.swayterm.toml`
*  Cross-platform: Linux, FreeBSD, macOS, Windows (via PowerShell)
*  Built-in **Git integration**: stage, commit, push and view diffs — without leaving the editor

🎯 **Ideal For:**

* Editing scripts, configs, and automation pipelines (YAML, INI, Dockerfile, etc.)
* Managing infrastructure-as-code and DevOps assets on the fly
* Working inside terminals, containers, or headless environments
* Server-side editing via SSH sessions

🧠 **Philosophy:**

> **"As lightweight as `nano`, as powerful as `vim`, as Git-aware as `Magit`, and written in Python — for modern DevOps."**

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

<br>

> [!Warning]
> **System Requirements:**
>  
> **Linux**  
> Editor **requires** either `xclip` or `xsel`  
> 
> **FreeBSD**  
> Editor **requires xclip only** (xsel removed from repositories)  
> 
> **All Systems:**  
> • Install required clipboard utility before first use  
> • Restart terminal after installation  

<br>

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

---

# DevOps Linters Integration for SwayPad

This repository provides a plugin module `lint_devops.py` to integrate fast and modern linters for DevOps and infrastructure-as-code files into the SwayPad text editor. It enables automatic linting of Bash, YAML, Dockerfiles, Terraform, Kubernetes, GitHub Actions, Ansible, Jsonnet, Lua, Nix, TOML, Go, C, and Rust files.

---

## 🔧 Requirements

Make sure you have the following tools installed, depending on your platform:

* **Python 3.11+**
* **pip** and/or **cargo** (Rust), **go** (Golang), **luarocks** (Lua)
* Access to your system package manager (`nix-env`, `apt`, `brew`, etc.)

---

## ⚙️ Installation

You can run the following script to install all supported linters:

```bash
chmod +x install_devops_linters.sh
./install_devops_linters.sh
```

You can also selectively install only some linters:

```bash
./install_devops_linters.sh --only go,rust,yaml
```

Or dry-run without installing:

```bash
./install_devops_linters.sh --dry-run
```

---

## 📦 Linters and Setup Instructions

Each linter below includes installation instructions and dependency requirements:

### ✅ Bash (shfmt)

* **Linter**: `shfmt`
* **Install**:

  ```bash
  nix-env -iA nixpkgs.shfmt       # NixOS
  sudo apt install -y shfmt       # Debian/Ubuntu
  brew install shfmt              # macOS
  ```

### ✅ YAML (yamlfmt)

* **Linter**: `yamlfmt` (requires Go)
* **Install Go**:

  ```bash
  sudo apt install golang         # Debian/Ubuntu
  nix-env -iA nixpkgs.go          # NixOS
  ```
* **Install yamlfmt**:

  ```bash
  go install github.com/google/yamlfmt/cmd/yamlfmt@latest
  ```

### ✅ Terraform (tfsec)

* **Linter**: `tfsec`
* **Install**:

  ```bash
  curl -s https://raw.githubusercontent.com/aquasecurity/tfsec/master/scripts/install_linux.sh | bash
  ```

### ✅ Dockerfile (hadolint)

* **Linter**: `hadolint`
* **Install**:

  ```bash
  wget -O hadolint https://github.com/hadolint/hadolint/releases/latest/download/hadolint-Linux-x86_64
  chmod +x hadolint && sudo mv hadolint /usr/local/bin/
  ```

### ✅ Kubernetes YAML (datree)

* **Linter**: `datree`
* **Install**:

  ```bash
  curl https://get.datree.io | /bin/bash
  ```

### ✅ GitHub Actions (actionlint)

* **Linter**: `actionlint` (Go)
* **Install**:

  ```bash
  go install github.com/rhysd/actionlint/cmd/actionlint@latest
  ```

### ✅ Ansible (ansible-lint)

* **Linter**: `ansible-lint`
* **Install**:

  ```bash
  pip install ansible-lint
  ```

### ✅ Jsonnet (jsonnetfmt)

* **Linter**: `jsonnetfmt`
* **Install**:

  ```bash
  sudo apt install jsonnet
  ```

### ✅ Helm (helm lint)

* **Tool**: `helm`
* **Install**:

  ```bash
  curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
  ```

### ✅ Docker Compose

* **Tool**: `docker-compose`
* **Install**:

  ```bash
  sudo apt install docker-compose
  ```

### ✅ Lua (luacheck)

* **Linter**: `luacheck`
* **Install**:

  ```bash
  sudo apt install luarocks
  sudo luarocks install luacheck
  ```

### ✅ Nix (nix-linter)

* **Linter**: `nix-linter`
* **Install**:

  ```bash
  nix-env -iA nixpkgs.nix-linter
  ```

### ✅ TOML (taplo)

* **Linter**: `taplo`
* **Install**:

  ```bash
  cargo install taplo-cli --locked
  ```

### ✅ Go (golangci-lint)

* **Linter**: `golangci-lint`
* **Install Go** (if not yet): see YAML section
* **Install**:

  ```bash
  go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest
  ```

### ✅ C (clang-tidy)

* **Linter**: `clang-tidy`
* **Install**:

  ```bash
  sudo apt install clang-tidy
  ```

### ✅ Rust (cargo clippy)

* **Linter**: `clippy`
* **Install**:

  ```bash
  rustup component add clippy
  ```

---

### 📌 License

This integration module is MIT-licensed. All linters are third-party tools with their own licenses.

---

### 💬 Questions & Contributions

Feel free to open issues or submit PRs for new linters, documentation improvements, or integration support.

<br>

---

<br>


## 📜 License

**GNU General Public License v3.0**  
Commercial use requires explicit permission.  
Full text available in [LICENSE](LICENSE).


<br>

### 📬 Contact

**Sergey Sobolewski**  

[![Email](https://img.shields.io/badge/Email-s.sobolewski@hotmail.com-blue?logo=protonmail)](mailto:s.sobolewski@hotmail.com)  

[![GitHub](https://img.shields.io/badge/GitHub-SSobol77-black?logo=github)](https://github.com/SSobol77)  

[![Website](https://img.shields.io/badge/Website-Cartesian_School-orange?logo=internet-explorer)](https://cartesianschool.com)

[![Discussions](https://img.shields.io/badge/Community-Discussions-blue?logo=github)](https://github.com/SSobol77/sway-pad/discussions)

[![Issues](https://img.shields.io/badge/Report-Bugs-red?logo=github)](https://github.com/SSobol77/Sway-pad/issues)

<br>
