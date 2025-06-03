<p align="center">
  <img src="https://github.com/user-attachments/assets/01bdf424-7dce-4a99-9631-de3b7e87313b" alt="swaypadm2" width="720">
</p>

<p align="center">
  <a href="https://www.gnu.org/licenses/gpl-3.0">
    <img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="License: GPL v3">
  </a>
  <img src="https://img.shields.io/badge/Python-3.11%2B-blue?logo=python" alt="Python Version">
  <img src="https://img.shields.io/badge/Linux-FCC624?logo=linux&logoColor=black" alt="Linux support">
  <img src="https://img.shields.io/badge/FreeBSD-AB2B28?logo=freebsd&logoColor=white" alt="FreeBSD support">
  <img src="https://img.shields.io/badge/Windows-0078D6?logo=windows&logoColor=white" alt="Windows support">
  <a href="https://github.com/SSobol77/sway-pad/pulls">
    <img src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg" alt="PRs Welcome">
  </a>
</p>

---

> **🚫 Ethical Restrictions**
>
> This software must not be used for:
>
> * Military applications or defense systems
> * Mass surveillance or tracking systems
> * Projects violating international human rights conventions

---

<h1 align="center">🌊 Sway-pad</h1>
<h3 align="center">Cross-platform Terminal Editor for Modern DevOps Engineers</h3>

---

<br>

## ✨ Overview

**Sway-pad** is a fast, minimal, and extensible terminal-based code editor written in Python — designed with the needs of DevOps engineers, SREs, and sysadmins in mind.

Whether you’re editing YAML inside a container or deploying infrastructure over SSH — Sway-pad provides a Git-aware, plugin-enabled, and keyboard-friendly environment without GUI overhead.


<br>

### 🔍 Why Sway-pad for DevOps?

* ⚡️ **Fast start-up**, minimal memory usage
* 🧩 **Built-in DevOps linter support**: Bash, YAML, Terraform, Docker, K8s, Ansible, Helm, etc.
* ⛓ **Zero GUI**: fully terminal-native, perfect for headless Linux/FreeBSD systems
* 🧠 **Modular config system** using `.toml` with per-user overrides
* 💬 **Syntax highlighting** powered by Pygments, with customizable themes
* 🎹 **Custom keybindings**: define any command or combo via `config.toml`
* 🔧 **Plugin-ready architecture**: drop-in support for linters, LSPs, or Git tools
* 🧰 **Full Git support**: stage, diff, commit, push — without leaving the terminal
* 🪶 **Lightweight design**: no daemons, no background agents — just your terminal and Python

<br>

---

<br>

### 🖥 Supported Platforms

| OS      | Support Level | Notes                                      |
| ------- | ------------- | ------------------------------------------ |
| Linux   | ✅ Full        | Works in X11, Wayland, TTY, SSH            |
| FreeBSD | ✅ Full        | `xclip` required for clipboard             |
| macOS   | ⚠️ Partial    | Clipboard/TTY requires `pbcopy` workaround |
| Windows | ⚠️ Partial    | Works via `Windows Terminal` and WSL       |

<br>

### 📚 Supported Formats (40+)

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

### ⚡ Quick Start

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

### 🔌 Configuration

Create or modify `~/.config/swaypad/config.toml` to customize your environment:

```toml
[editor]
theme = "nord"
font_family = "Fira Code"
tab_size = 4
auto_indent = true

[keybindings]
save = "ctrl+s"
quit = "ctrl+q"
run_linter = "f8"
split_pane = "ctrl+alt+enter"

[plugins]
lsp_enabled = true
git_diff = { enabled = true, hotkey = "f5" }
```

You can override defaults per project or user, and dynamically reload config without restarting the editor.

---

### 💡 Use Cases

* **Edit Kubernetes manifests** over SSH directly inside a running pod:

  ```bash
  sway-pad /etc/k8s/deploy.yaml
  ```

* **Write Terraform files** and check them live with `tfsec`:

  ```bash
  sway-pad main.tf
  ```

* **Work on Dockerfiles in CI environments**:

  ```bash
  sway-pad Dockerfile
  ```

* **Configure Nix and TOML projects** side-by-side:

  ```bash
  sway-pad flake.nix config.toml
  ```

* **Review Git history and patch files on remote server**:

  ```bash
  sway-pad .gitignore .gitlab-ci.yml
  ```

<br>

---

<br>

# DevOps Linters Integration for SwayPad

This repository provides a plugin module `lint_devops.py` to integrate fast and modern linters for DevOps and infrastructure-as-code files into the SwayPad text editor. It enables automatic linting of Python, Bash, YAML, Dockerfiles, Terraform, Kubernetes, GitHub Actions, Ansible, Jsonnet, Lua, Nix, TOML, Go, C, and Rust files.

<br>

---

<br>

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

<br>

---

<br>

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

<br>

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

<br>

### 📜 License

- **Sway-pad** is licensed under the **GNU General Public License v3.0**  
  Commercial use requires explicit permission from the author.  
  See full text in [LICENSE](LICENSE).

- **DevOps Linters Plugin** (`lint_devops.py`) is released under the **MIT License**.  
  Each linter it invokes is a third-party tool and follows its own license.

<br>

### 📬 Contact

**Sergey Sobolewski**  

[![Email](https://img.shields.io/badge/Email-s.sobolewski@hotmail.com-blue?logo=protonmail)](mailto:s.sobolewski@hotmail.com)  

[![GitHub](https://img.shields.io/badge/GitHub-SSobol77-black?logo=github)](https://github.com/SSobol77)  

[![Website](https://img.shields.io/badge/Website-Cartesian_School-orange?logo=internet-explorer)](https://cartesianschool.com)

[![Discussions](https://img.shields.io/badge/Community-Discussions-blue?logo=github)](https://github.com/SSobol77/sway-pad/discussions)

[![Issues](https://img.shields.io/badge/Report-Bugs-red?logo=github)](https://github.com/SSobol77/Sway-pad/issues)

<br>
