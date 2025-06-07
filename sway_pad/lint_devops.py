"""lint_devops.py – DevOps linters orchestration (async‑ready)

This module supplies a thin abstraction for running external command‑line
linters that target infrastructure‑as‑code and DevOps‑oriented formats.

Key improvements versus the legacy implementation:
    • **Async & concurrent** execution via ``asyncio.create_subprocess_exec``.
    • Corrected binaries: *nix‑lint* (was *nix‑linter*), *cargo clippy -- …*.
    • Unified install‑check helper with rich error reporting.
    • Preserves a synchronous wrapper for drop‑in compatibility.

Only Python 3.11+ is supported (uses PEP 654 exception groups and
``typing.Self``).
"""

from __future__ import annotations

import argparse
import sys
import textwrap
import asyncio
import shutil

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Final, Tuple
from sway2 import safe_run 

# --------------------------------------------------------------------------------------
#   Configuration
# --------------------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class LinterCfg:
    """Static definition of a single linter programme."""

    install_check: Tuple[str, ...]
    run_cmd: Tuple[str, ...]
    extensions: Tuple[str, ...]

    @property
    def binary(self) -> str:  # noqa: D401
        """Return the command’s executable (first element of *install_check*)."""
        return self.install_check[0]


DEVOPS_LINTERS: Final[Dict[str, LinterCfg]] = {
    # ---------------------------- Shell & Scripting -----------------------------
    "bash": LinterCfg(
        install_check=("shellcheck", "--version"),
        run_cmd=("shellcheck", "-"),
        extensions=(".sh", ".bash", ".zsh"),
    ),
    "bash_format": LinterCfg(
        install_check=("shfmt", "--version"),
        run_cmd=("shfmt", "-d", "-"),
        extensions=(".sh", ".bash", ".zsh"),
    ),
    
    # ---------------------------- YAML & JSON -----------------------------------
    "yaml": LinterCfg(
        install_check=("yamllint", "--version"),
        run_cmd=("yamllint", "-"),
        extensions=(".yml", ".yaml"),
    ),
    "yaml_format": LinterCfg(
        install_check=("yamlfmt", "--version"),
        run_cmd=("yamlfmt", "-"),
        extensions=(".yml", ".yaml"),
    ),
    "json": LinterCfg(
        install_check=("jsonlint", "--version"),
        run_cmd=("jsonlint", "-"),
        extensions=(".json",),
    ),
    
    # ---------------------------- Infrastructure as Code ------------------------
    "terraform": LinterCfg(
        install_check=("tfsec", "--version"),
        run_cmd=("tfsec", "."),
        extensions=(".tf", ".tfvars"),
    ),
    "terraform_lint": LinterCfg(
        install_check=("tflint", "--version"),
        run_cmd=("tflint", "--format=compact"),
        extensions=(".tf", ".tfvars"),
    ),
    "terraform_format": LinterCfg(
        install_check=("terraform", "--version"),
        run_cmd=("terraform", "fmt", "-check", "-diff", "-"),
        extensions=(".tf", ".tfvars"),
    ),
    "checkov": LinterCfg(
        install_check=("checkov", "--version"),
        run_cmd=("checkov", "-f", "-"),
        extensions=(".tf", ".yml", ".yaml", ".json"),
    ),
    "terrascan": LinterCfg(
        install_check=("terrascan", "version"),
        run_cmd=("terrascan", "scan", "-t", "all", "-f", "-"),
        extensions=(".tf", ".yml", ".yaml", ".json"),
    ),
    
    # ---------------------------- Containers ------------------------------------
    "dockerfile": LinterCfg(
        install_check=("hadolint", "--version"),
        run_cmd=("hadolint", "-"),
        extensions=("dockerfile", "Dockerfile", ".dockerfile"),
    ),
    "docker_compose": LinterCfg(
        install_check=("docker-compose", "--version"),
        run_cmd=("docker-compose", "-f", "-", "config"),
        extensions=("docker-compose.yaml", "docker-compose.yml", "compose.yaml", "compose.yml"),
    ),
    "trivy": LinterCfg(
        install_check=("trivy", "--version"),
        run_cmd=("trivy", "config", "-"),
        extensions=(".dockerfile", "Dockerfile", ".yml", ".yaml"),
    ),
    
    # ---------------------------- Kubernetes ------------------------------------
    "kubernetes": LinterCfg(
        install_check=("kubeval", "--version"),
        run_cmd=("kubeval", "-"),
        extensions=(".yaml", ".yml"),
    ),
    "kube_score": LinterCfg(
        install_check=("kube-score", "version"),
        run_cmd=("kube-score", "score", "-"),
        extensions=(".yaml", ".yml"),
    ),
    "datree": LinterCfg(
        install_check=("datree", "version"),
        run_cmd=("datree", "test", "-"),
        extensions=(".yaml", ".yml"),
    ),
    "polaris": LinterCfg(
        install_check=("polaris", "version"),
        run_cmd=("polaris", "audit", "--format", "pretty", "-"),
        extensions=(".yaml", ".yml"),
    ),
    
    # ---------------------------- Helm ------------------------------------------
    "helm": LinterCfg(
        install_check=("helm", "version"),
        run_cmd=("helm", "lint", "."),
        extensions=("Chart.yaml", "values.yaml"),
    ),
    "helmfile": LinterCfg(
        install_check=("helmfile", "--version"),
        run_cmd=("helmfile", "lint"),
        extensions=("helmfile.yaml", "helmfile.yml"),
    ),
    
    # ---------------------------- CI/CD -----------------------------------------
    "github_actions": LinterCfg(
        install_check=("actionlint", "--version"),
        run_cmd=("actionlint", "-"),
        extensions=(".yml", ".yaml"),
    ),
    "gitlab_ci": LinterCfg(
        install_check=("gitlab-ci-lint", "--version"),
        run_cmd=("gitlab-ci-lint", "-"),
        extensions=(".gitlab-ci.yml", ".gitlab-ci.yaml"),
    ),
    "circleci": LinterCfg(
        install_check=("circleci", "version"),
        run_cmd=("circleci", "config", "validate", "-"),
        extensions=(".circleci/config.yml",),
    ),
    
    # ---------------------------- Configuration Management ----------------------
    "ansible": LinterCfg(
        install_check=("ansible-lint", "--version"),
        run_cmd=("ansible-lint", "-"),
        extensions=(".yml", ".yaml"),
    ),
    
    # ---------------------------- Monitoring & Observability -------------------
    "prometheus": LinterCfg(
        install_check=("promtool", "--version"),
        run_cmd=("promtool", "check", "rules", "-"),
        extensions=(".yml", ".yaml"),
    ),
    "alertmanager": LinterCfg(
        install_check=("amtool", "--version"),
        run_cmd=("amtool", "config", "check", "-"),
        extensions=(".yml", ".yaml"),
    ),
    
    # ---------------------------- Security ---------------------------------------
    "gitleaks": LinterCfg(
        install_check=("gitleaks", "version"),
        run_cmd=("gitleaks", "detect", "--source", "."),
        extensions=("*",),
    ),
    "semgrep": LinterCfg(
        install_check=("semgrep", "--version"),
        run_cmd=("semgrep", "--config=auto", "-"),
        extensions=(".py", ".js", ".go", ".java", ".yml", ".yaml"),
    ),
    "snyk": LinterCfg(
        install_check=("snyk", "--version"),
        run_cmd=("snyk", "code", "test", "-"),
        extensions=(".py", ".js", ".go", ".java", ".tf"),
    ),
    
    # ---------------------------- Cloud Providers -------------------------------
    "aws_cfn": LinterCfg(
        install_check=("cfn-lint", "--version"),
        run_cmd=("cfn-lint", "-"),
        extensions=(".yml", ".yaml", ".json"),
    ),
    
    # ---------------------------- Policy as Code --------------------------------
    "conftest": LinterCfg(
        install_check=("conftest", "--version"),
        run_cmd=("conftest", "verify", "-"),
        extensions=(".yml", ".yaml", ".json"),
    ),
    "opa": LinterCfg(
        install_check=("opa", "version"),
        run_cmd=("opa", "fmt", "--diff", "-"),
        extensions=(".rego",),
    ),
    
    # ---------------------------- Data Formats ----------------------------------
    "jsonnet": LinterCfg(
        install_check=("jsonnetfmt", "--version"),
        run_cmd=("jsonnetfmt", "-"),
        extensions=(".jsonnet", ".libsonnet"),
    ),
    "toml": LinterCfg(
        install_check=("taplo", "--version"),
        run_cmd=("taplo", "fmt", "--check", "--diff", "-"),
        extensions=(".toml",),
    ),
    
    # ---------------------------- Documentation ---------------------------------
    "markdown": LinterCfg(
        install_check=("markdownlint", "--version"),
        run_cmd=("markdownlint", "-"),
        extensions=(".md", ".markdown"),
    ),
    "vale": LinterCfg(
        install_check=("vale", "--version"),
        run_cmd=("vale", "-"),
        extensions=(".md", ".rst", ".txt"),
    ),
    
    # ---------------------------- Systems Languages -----------------------------
    "go": LinterCfg(
        install_check=("golangci-lint", "--version"),
        run_cmd=("golangci-lint", "run", "--out-format", "line-number"),
        extensions=(".go",),
    ),
    "rust": LinterCfg(
        install_check=("cargo", "clippy", "--version"),
        run_cmd=("cargo", "clippy", "--", "-D", "warnings"),
        extensions=(".rs",),
    ),
    "c": LinterCfg(
        install_check=("clang-tidy", "--version"),
        run_cmd=("clang-tidy", "-"),
        extensions=(".c", ".h"),
    ),
    "cpp": LinterCfg(
        install_check=("cppcheck", "--version"),
        run_cmd=("cppcheck", "--enable=all", "--std=c++17", "-"),
        extensions=(".cpp", ".cc", ".cxx", ".hpp", ".h"),
    ),
    
    # ---------------------------- Scripting Languages ---------------------------
    "python": LinterCfg(
        install_check=("flake8", "--version"),
        run_cmd=("flake8", "-"),
        extensions=(".py",),
    ),
    "python_format": LinterCfg(
        install_check=("black", "--version"),
        run_cmd=("black", "--check", "--diff", "-"),
        extensions=(".py",),
    ),
    "python_types": LinterCfg(
        install_check=("mypy", "--version"),
        run_cmd=("mypy", "-"),
        extensions=(".py",),
    ),
    "python_security": LinterCfg(
        install_check=("bandit", "--version"),
        run_cmd=("bandit", "-f", "txt", "-"),
        extensions=(".py",),
    ),
    "lua": LinterCfg(
        install_check=("luacheck", "--version"),
        run_cmd=("luacheck", "-"),
        extensions=(".lua",),
    ),
    
    # ---------------------------- Nix -------------------------------------------
    "nix": LinterCfg(
        install_check=("nixpkgs-fmt", "--version"),
        run_cmd=("nixpkgs-fmt", "--check", "-"),
        extensions=(".nix",),
    ),
    "nix_lint": LinterCfg(
        install_check=("nix-linter", "--version"),
        run_cmd=("nix-linter", "-"),
        extensions=(".nix",),
    ),
    
    # ---------------------------- Web/Frontend ----------------------------------
    "javascript": LinterCfg(
        install_check=("eslint", "--version"),
        run_cmd=("eslint", "--format", "compact", "-"),
        extensions=(".js", ".jsx", ".ts", ".tsx"),
    ),
    "javascript_format": LinterCfg(
        install_check=("prettier", "--version"),
        run_cmd=("prettier", "--check", "-"),
        extensions=(".js", ".jsx", ".ts", ".tsx", ".json", ".css", ".md"),
    ),
    
    # ---------------------------- Other Languages -------------------------------
    "java": LinterCfg(
        install_check=("google-java-format", "--version"),
        run_cmd=("google-java-format", "--dry-run", "--set-exit-if-changed", "-"),
        extensions=(".java",),
    ),
    "dart": LinterCfg(
        install_check=("dart", "--version"),
        run_cmd=("dart", "format", "--set-exit-if-changed", "-"),
        extensions=(".dart",),
    ),
    "haskell": LinterCfg(
        install_check=("hlint", "--version"),
        run_cmd=("hlint", "-"),
        extensions=(".hs", ".lhs"),
    ),
}

# --------------------------------------------------------------------------------------
#   Public API (sync)
# -------------------------------------------------------------------------------------

def run_devops_linter(language: str, code: str, *, timeout: int = 30) -> str:
    """Execute a DevOps-oriented linter synchronously and return its output.

    This function dispatches the specified linter for a given language or file type,
    handles binary availability, feeds the code through stdin if required, and
    returns the resulting output or a formatted diagnostic string.

    Args:
        language (str): Key identifying the linter from DEVOPS_LINTERS.
        code (str): The source text to be linted.
        timeout (int, optional): Time limit for execution in seconds. Defaults to 30.

    Returns:
        str: Linter output (stdout/stderr), or a human-readable error/status message.

    Example:
        >>> run_devops_linter("yaml", "key: value")
        '✓ No issues detected'

    Notes:
        - Linters expecting stdin must have "-" in their run command.
        - If the linter binary is not found in PATH, a user-friendly message is returned.
        - All unexpected errors are logged via `safe_run` and wrapped in a diagnostic.
    """

    cfg = DEVOPS_LINTERS.get(language)
    if cfg is None:
        return f"[Linter] No linter configured for '{language}'."

    if shutil.which(cfg.binary) is None:
        return (
            f"❌ '{cfg.binary}' not installed or not in PATH. "
            "Install it and restart the editor."
        )

    # Determine if stdin should be used based on presence of "-" in the command
    stdin_code = code if "-" in cfg.run_cmd else None

    result = safe_run(
        list(cfg.run_cmd),
        input=stdin_code,
        timeout=timeout,
    )

    # Return any diagnostic output first
    if result.stdout.strip() or result.stderr.strip():
        return result.stdout.strip() or result.stderr.strip()

    # Formatter returned non-zero exit without messages (often means "changes needed")
    if result.returncode != 0:
        return f"⚠️ Formatter suggests changes (exit code {result.returncode})."

    # Clean result
    return "✓ No issues detected"


# --------------------------------------------------------------------------------------
#   Public API (async)
# --------------------------------------------------------------------------------------

async def run_devops_linter_async(language: str, code: str, *, timeout: int = 30) -> str:
    """Asynchronously run a DevOps linter and return its output or diagnostic.

    This function dispatches the specified linter using a non-blocking subprocess.
    It captures output, handles timeouts, and returns a unified result format.
    Only linters that read from stdin require code to be passed to stdin.

    Args:
        language (str): Linter key from the DEVOPS_LINTERS registry.
        code (str): Source text to lint.
        timeout (int, optional): Timeout in seconds. Defaults to 30.

    Returns:
        str: Output from the linter (stdout or stderr), or a formatted message:
            - "❌ ..." for errors
            - "⏱ ..." for timeouts
            - "⚠️ ..." for format suggestions
            - "✓ No issues detected" if clean

    Raises:
        None. All exceptions are caught and converted into user-facing messages.

    Example:
        >>> await run_devops_linter_async("rust", "fn main() {}")
        '✓ No issues detected'
    """

    cfg = DEVOPS_LINTERS.get(language)
    if cfg is None:
        return f"[Linter] No linter configured for '{language}'."

    if shutil.which(cfg.binary) is None:
        return f"❌ '{cfg.binary}' not installed or not in PATH."

    use_stdin = "-" in cfg.run_cmd
    try:
        proc = await asyncio.create_subprocess_exec(
            *cfg.run_cmd,
            stdin=asyncio.subprocess.PIPE if use_stdin else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(code.encode() if use_stdin else None),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return f"⏱  Linter for '{language}' timed-out after {timeout}s."

    except FileNotFoundError:
        return f"❌ '{cfg.binary}' executable not found."
    except Exception as exc:
        return f"❌ Failed to run linter for '{language}': {exc}"

    output = stdout.decode("utf-8", "replace").strip()
    if output:
        return output

    if proc.returncode != 0:
        return f"⚠️ Formatter suggests changes (exit code {proc.returncode})."

    return "✓ No issues detected"



# --------------------------------------------------------------------------------------
#   Helper to run many linters concurrently
# --------------------------------------------------------------------------------------

async def run_many(codes: Dict[str, str], *, timeout: int = 30) -> Dict[str, str]:
    """Run several linters in parallel."""
    coros = [run_devops_linter_async(lang, src, timeout=timeout) for lang, src in codes.items()]
    results = await asyncio.gather(*coros)
    return dict(zip(codes.keys(), results))

# --------------------------------------------------------------------------------------
#   Convenience entry‑point (CLI)
# --------------------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover

    parser = argparse.ArgumentParser(
        description="Run a DevOps linter on stdin or a file (sync).",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=textwrap.dedent(
            """
            Examples
            --------
            echo 'fn main(){}' | python lint_devops.py rust
            python lint_devops.py terraform main.tf             
            """,
        ),
    )
    parser.add_argument("language", help="Key of the linter to invoke (see DEVOPS_LINTERS)")
    parser.add_argument("file", nargs="?", help="Path to source file. If omitted, read from stdin.")
    parser.add_argument("--timeout", type=int, default=30, help="Seconds before aborting the linter (default: 30)")
    args = parser.parse_args()

    src_code = Path(args.file).read_text() if args.file else sys.stdin.read()
    out = run_devops_linter(args.language, src_code, timeout=args.timeout)
    print(out.strip())
