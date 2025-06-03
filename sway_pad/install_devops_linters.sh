#!/usr/bin/env bash
set -e

show_usage() {
    echo "Usage: $0 [--dry-run] [--only <linter1,linter2,...>]"
    exit 1
}

DRY_RUN=false
ONLY=""
for arg in "$@"; do
    case $arg in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --only)
            ONLY="$2"
            shift 2
            ;;
        *)
            show_usage
            ;;
    esac
done

should_run() {
    [ -z "$ONLY" ] && return 0
    [[ ",$ONLY," == *",$1,"* ]]
}

install() {
    local name="$1"
    local cmd="$2"
    if should_run "$name"; then
        echo "🔧 Installing $name..."
        $DRY_RUN && echo "[DRY RUN] $cmd" || eval "$cmd"
    fi
}

OS=$(uname -s)
if [ "$OS" = "Darwin" ]; then
    PLATFORM="macos"
elif [ -f /etc/nixos/configuration.nix ]; then
    PLATFORM="nixos"
elif [ -f /etc/debian_version ]; then
    PLATFORM="debian"
elif [ -f /etc/arch-release ]; then
    PLATFORM="arch"
else
    echo "⚠️ Unsupported system. Please install manually."
    exit 1
fi

echo "📦 Detected platform: $PLATFORM"
echo "🏗️ Starting DevOps linter installation..."

case "$PLATFORM" in
nixos)
    install shfmt 'nix-env -iA nixpkgs.shfmt'
    install yamlfmt 'go install github.com/google/yamlfmt/cmd/yamlfmt@latest'
    install tfsec 'nix-env -iA nixpkgs.tfsec'
    install hadolint 'nix-env -iA nixpkgs.hadolint'
    install datree 'curl https://get.datree.io | /bin/bash'
    install actionlint 'go install github.com/rhysd/actionlint/cmd/actionlint@latest'
    install ansible-lint 'pip install ansible-lint'
    install jsonnetfmt 'nix-env -iA nixpkgs.jsonnet'
    install helm 'nix-env -iA nixpkgs.kubernetes-helm'
    install luacheck 'nix-env -iA nixpkgs.luacheck'
    install nix-linter 'nix-env -iA nixpkgs.nix-linter'
    install taplo 'cargo install taplo-cli --locked'
    install golangci-lint 'go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest'
    install clang-tidy 'nix-env -iA nixpkgs.clang-tools'
    install clippy 'rustup component add clippy'
    ;;
debian)
    sudo apt update
    install shfmt 'sudo apt install -y shfmt'
    install yamlfmt 'go install github.com/google/yamlfmt/cmd/yamlfmt@latest'
    install tfsec 'curl -s https://raw.githubusercontent.com/aquasecurity/tfsec/master/scripts/install_linux.sh | bash'
    install hadolint 'wget -O hadolint https://github.com/hadolint/hadolint/releases/latest/download/hadolint-Linux-x86_64 && chmod +x hadolint && sudo mv hadolint /usr/local/bin/'
    install datree 'curl https://get.datree.io | /bin/bash'
    install actionlint 'go install github.com/rhysd/actionlint/cmd/actionlint@latest'
    install ansible-lint 'pip install ansible-lint'
    install jsonnetfmt 'sudo apt install -y jsonnet'
    install helm 'curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash'
    install luacheck 'sudo apt install -y luarocks && sudo luarocks install luacheck'
    install taplo 'cargo install taplo-cli --locked'
    install golangci-lint 'go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest'
    install clang-tidy 'sudo apt install -y clang-tidy'
    install clippy 'rustup component add clippy'
    ;;
esac

echo "✅ Installation complete."
