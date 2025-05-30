import json
import logging
import subprocess
import os
from pygls import client
from typing import List, Dict, Optional

class LSPClient:
    def __init__(self, language: str, server_cmd: List[str]):
        self.language = language
        self.server_cmd = server_cmd
        self.client = client.Client()
        self.process = None
        self.diagnostics = []
        self.initialized = False

    def start(self):
        """Запускает LSP-сервер."""
        try:
            self.process = subprocess.Popen(
                self.server_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            self.client.start_io(self.process.stdin, self.process.stdout)
            self.initialize()
        except Exception as e:
            logging.error(f"Failed to start LSP server: {self.language}: {e}")

    def initialize(self):
        """Инициализирует LSP-сервер."""
        init_message = {
            "jsonrpc": "2.0",
            "id": "init",
            "method": "initialize",
            "params": {
                "processId": os.getpid(),
                "rootUri": "file:///",
                "capabilities": {}
            }
        }
        self.client.send_request(json.dumps(init_message))
        self.initialized = True

    def send_diagnostic_request(self, text: str, file_path: str):
        """Отправляет запрос на диагностику."""
        if not self.initialized:
            return
        did_open = {
            "jsonrpc": "2.0",
            "method": "textDocument/didOpen",
            "params": {
                "textDocument": {
                    "uri": f"file://{file_path}",
                    "languageId": self.language,
                    "version": 1,
                    "text": text
                }
            }
        }
        self.client.send_notification(json.dumps(did_open))

    def receive_diagnostics(self) -> List[Dict]:
        """Получает диагностики от сервера."""
        return self.diagnostics

    def stop(self):
        """Останавливает LSP-сервер."""
        if self.process:
            self.client.exit()
            self.process.terminate()
            self.initialized = False
