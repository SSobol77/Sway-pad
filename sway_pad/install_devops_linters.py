#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DevOps Linters Installer/Uninstaller Module"""

import platform
import shutil
import subprocess
import logging
from typing import List, Dict


class DevOpsLinterInstaller:
    """Install DevOps linters for supported OSes."""

    def __init__(self, verbose: bool = False):
        self.os = platform.system().lower()
        self.pkg_mgr = self.detect_package_manager()
        self.verbose = verbose
        
        # Настройка логирования
        logging.basicConfig(
            level=logging.INFO if verbose else logging.WARNING,
            format='[%(levelname)s] %(message)s'
        )
        self.logger = logging.getLogger(__name__)

    def detect_package_manager(self) -> str:
        """Определяет доступный пакетный менеджер."""
        if self.os == "linux":
            for cmd in ("apt", "pacman", "dnf", "yum", "zypper"):
                if shutil.which(cmd):
                    return cmd
        elif self.os == "darwin":
            if shutil.which("brew"):
                return "brew"
        elif self.os == "freebsd":
            if shutil.which("pkg"):
                return "pkg"
        
        # Проверяем nix для всех систем
        if shutil.which("nix-env"):
            return "nix"
            
        raise RuntimeError(f"Неподдерживаемая платформа или пакетный менеджер для {self.os}")

    def get_package_mapping(self) -> Dict[str, str]:
        """Возвращает маппинг имен пакетов для разных менеджеров."""
        mappings = {
            "apt": {
                "shfmt": "shfmt",
                "hadolint": "hadolint", 
                "ansible-lint": "ansible-lint",
                "golangci-lint": "golangci-lint",
                "shellcheck": "shellcheck",
                "yamllint": "yamllint"
            },
            "pacman": {
                "shfmt": "shfmt",
                "hadolint": "hadolint",
                "shellcheck": "shellcheck",
                "golangci-lint": "golangci-lint"
            },
            "brew": {
                "shfmt": "shfmt",
                "hadolint": "hadolint",
                "ansible-lint": "ansible-lint", 
                "golangci-lint": "golangci-lint",
                "shellcheck": "shellcheck",
                "yamllint": "yamllint",
                "tfsec": "tfsec"
            }
        }
        return mappings.get(self.pkg_mgr, {})

    def check_if_installed(self, linter: str) -> bool:
        """Проверяет, установлен ли уже линтер."""
        return shutil.which(linter) is not None

    def install_single_linter(self, linter: str) -> bool:
        """Устанавливает один линтер."""
        # Проверяем, не установлен ли уже
        if self.check_if_installed(linter):
            self.logger.info(f"{linter} уже установлен")
            return True

        # Получаем правильное имя пакета
        package_mapping = self.get_package_mapping()
        package_name = package_mapping.get(linter, linter)
        
        self.logger.info(f"Устанавливаем: {linter} (пакет: {package_name})")
        
        try:
            cmd = self._get_install_command(package_name)
            subprocess.run(
                cmd, 
                check=True, 
                capture_output=not self.verbose,
                text=True
            )
            
            # Проверяем успешность установки
            if self.check_if_installed(linter):
                self.logger.info(f"✓ {linter} успешно установлен")
                return True
            else:
                self.logger.warning(f"⚠ {linter} установлен, но не найден в PATH")
                return False
                
        except subprocess.CalledProcessError as e:
            self.logger.error(f"✗ Ошибка установки {linter}: {e}")
            return False
        except Exception as e:
            self.logger.error(f"✗ Неожиданная ошибка при установке {linter}: {e}")
            return False

    def _get_install_command(self, package_name: str) -> List[str]:
        """Возвращает команду установки для текущего пакетного менеджера."""
        commands = {
            "apt": ["sudo", "apt", "install", "-y", package_name],
            "pacman": ["sudo", "pacman", "-Sy", "--noconfirm", package_name],
            "dnf": ["sudo", "dnf", "install", "-y", package_name],
            "yum": ["sudo", "yum", "install", "-y", package_name],
            "zypper": ["sudo", "zypper", "install", "-y", package_name],
            "brew": ["brew", "install", package_name],
            "pkg": ["sudo", "pkg", "install", "-y", package_name],
            "nix": ["nix-env", "-iA", f"nixpkgs.{package_name}"]
        }
        
        if self.pkg_mgr not in commands:
            raise RuntimeError(f"Неизвестный пакетный менеджер: {self.pkg_mgr}")
            
        return commands[self.pkg_mgr]

    def install(self, linters: List[str]) -> Dict[str, bool]:
        """Устанавливает список линтеров."""
        results = {}
        successful = 0
        
        self.logger.info(f"Начинаем установку {len(linters)} линтеров...")
        self.logger.info(f"Пакетный менеджер: {self.pkg_mgr}")
        
        for linter in linters:
            results[linter] = self.install_single_linter(linter)
            if results[linter]:
                successful += 1
        
        self.logger.info(f"Установка завершена: {successful}/{len(linters)} успешно")
        return results


class DevOpsLinterUninstaller:
    """Uninstall DevOps linters and clean up."""

    def __init__(self, pkg_mgr: str, verbose: bool = False):
        self.pkg_mgr = pkg_mgr
        self.verbose = verbose
        
        logging.basicConfig(
            level=logging.INFO if verbose else logging.WARNING,
            format='[%(levelname)s] %(message)s'
        )
        self.logger = logging.getLogger(__name__)

    def check_if_installed(self, linter: str) -> bool:
        """Проверяет, установлен ли линтер."""
        return shutil.which(linter) is not None

    def uninstall_single_linter(self, linter: str) -> bool:
        """Удаляет один линтер."""
        if not self.check_if_installed(linter):
            self.logger.info(f"{linter} не установлен")
            return True

        self.logger.info(f"Удаляем: {linter}")
        
        try:
            cmd = self._get_uninstall_command(linter)
            subprocess.run(
                cmd, 
                check=True, 
                capture_output=not self.verbose,
                text=True
            )
            
            # Проверяем успешность удаления
            if not self.check_if_installed(linter):
                self.logger.info(f"✓ {linter} успешно удален")
                return True
            else:
                self.logger.warning(f"⚠ {linter} все еще найден после удаления")
                return False
                
        except subprocess.CalledProcessError as e:
            self.logger.error(f"✗ Ошибка удаления {linter}: {e}")
            return False
        except Exception as e:
            self.logger.error(f"✗ Неожиданная ошибка при удалении {linter}: {e}")
            return False

    def _get_uninstall_command(self, package_name: str) -> List[str]:
        """Возвращает команду удаления для текущего пакетного менеджера."""
        commands = {
            "apt": ["sudo", "apt", "remove", "-y", package_name],
            "pacman": ["sudo", "pacman", "-Rns", "--noconfirm", package_name],
            "dnf": ["sudo", "dnf", "remove", "-y", package_name],
            "yum": ["sudo", "yum", "remove", "-y", package_name],
            "zypper": ["sudo", "zypper", "rm", "-y", package_name],
            "brew": ["brew", "uninstall", package_name],
            "pkg": ["sudo", "pkg", "delete", "-y", package_name],
            "nix": ["nix-env", "-e", package_name]
        }
        
        if self.pkg_mgr not in commands:
            raise RuntimeError(f"Неизвестный пакетный менеджер: {self.pkg_mgr}")
            
        return commands[self.pkg_mgr]

    def uninstall(self, linters: List[str]) -> Dict[str, bool]:
        """Удаляет список линтеров."""
        results = {}
        successful = 0
        
        self.logger.info(f"Начинаем удаление {len(linters)} линтеров...")
        
        for linter in linters:
            results[linter] = self.uninstall_single_linter(linter)
            if results[linter]:
                successful += 1
        
        self.logger.info(f"Удаление завершено: {successful}/{len(linters)} успешно")
        return results


# Обновленный список популярных линтеров
DEFAULT_LINTERS = [
    # Shell/Bash
    "shellcheck", "shfmt",
    
    # YAML/JSON
    "yamllint", "jsonlint",
    
    # Docker
    "hadolint",
    
    # Infrastructure as Code
    "tfsec", "terrascan",
    
    # CI/CD
    "actionlint",
    
    # Configuration Management
    "ansible-lint",
    
    # Go
    "golangci-lint",
    
    # Python (часто уже установлены с pip)
    "flake8", "black", "mypy",
    
    # Web
    "eslint", "prettier"
]


def main():
    """Пример использования модуля."""
    try:
        installer = DevOpsLinterInstaller(verbose=True)
        print(f"Обнаружен пакетный менеджер: {installer.pkg_mgr}")
        print(f"Операционная система: {installer.os}")
        
        # Устанавливаем базовые линтеры
        basic_linters = ["shellcheck", "yamllint", "hadolint"]
        results = installer.install(basic_linters)
        
        print("\nРезультаты установки:")
        for linter, success in results.items():
            status = "✓" if success else "✗"
            print(f"{status} {linter}")
            
    except Exception as e:
        print(f"Ошибка: {e}")


if __name__ == "__main__":
    main()