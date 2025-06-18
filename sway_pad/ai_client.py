# ai_client.py

import aiohttp
import os
import json
import logging
import asyncio
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

# Получаем логгер, чтобы сообщения соответствовали общей системе логирования
logger = logging.getLogger(__name__)

class BaseAiClient(ABC):
    """Абстрактный базовый класс для AI-клиентов."""
    def __init__(self, model: str, api_key: str):
        if not api_key:
            raise ValueError(f"API key for {self.__class__.__name__} is missing.")
        self.model = model
        self.api_key = api_key
        self.session: Optional[aiohttp.ClientSession] = None
        logger.info(f"Initialized {self.__class__.__name__} for model {self.model}")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            logger.debug("Creating new aiohttp.ClientSession")
            self.session = aiohttp.ClientSession()
        return self.session

    @abstractmethod
    async def ask_async(self, prompt: str, system_msg: str) -> str:
        pass

    async def close(self):
        if self.session and not self.session.closed:
            logger.debug("Closing aiohttp.ClientSession")
            await self.session.close()

class OpenAiClient(BaseAiClient):
    """Клиент для OpenAI API."""
    API_URL = "https://api.openai.com/v1/chat/completions"

    async def ask_async(self, prompt: str, system_msg: str = "You are a helpful assistant.") -> str:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_msg}, {"role": "user", "content": prompt}]
        }
        logger.debug("Sending request to OpenAI API...")
        try:
            session = await self._get_session()
            async with session.post(self.API_URL, headers=headers, json=body, timeout=90) as response:
                logger.info(f"Received response from OpenAI with status: {response.status}")
                response_text = await response.text()
                if response.status != 200:
                    logger.error(f"OpenAI API Error {response.status}: {response_text}")
                    return f"OpenAI Error {response.status}: {response_text[:200]}..."
                data = json.loads(response_text)
                return data.get("choices", [{}])[0].get("message", {}).get("content", "Empty response").strip()
        except asyncio.TimeoutError:
            logger.error("Request to OpenAI API timed out.")
            return "Error: Request to OpenAI timed out."
        except Exception as e:
            logger.error(f"An unexpected error occurred in OpenAiClient: {e}", exc_info=True)
            return f"Error: An unexpected error occurred: {e}"


class GeminiClient(BaseAiClient):
    """Клиент для Google Gemini API."""
    API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    async def ask_async(self, prompt: str, system_msg: str = "You are a helpful assistant.") -> str:
        url = self.API_URL_TEMPLATE.format(model=self.model, api_key=self.api_key)
        headers = {"Content-Type": "application/json"}
        body = {"contents": [{"parts": [{"text": f"{system_msg}\n\n{prompt}"}]}]}
        
        logger.debug(f"Sending request to Gemini API: {url}")
        logger.debug(f"Request body (preview): {str(body)[:200]}...")

        try:
            session = await self._get_session()
            async with session.post(url, headers=headers, json=body, timeout=90) as response:
                logger.info(f"Received response from Gemini with status: {response.status}")
                response_text = await response.text()

                if response.status != 200:
                    logger.error(f"Gemini API Error {response.status}: {response_text}")
                    return f"Gemini Error {response.status}: {response_text[:200]}..."

                data = json.loads(response_text)
                logger.debug(f"Gemini response data: {data}")
                
                candidates = data.get("candidates")
                if not candidates:
                    if "error" in data:
                        return f"Gemini API Error: {data['error'].get('message', 'Unknown error')}"
                    return "Error: Gemini response has no candidates. This might be due to safety filters."

                content = candidates[0].get("content", {})
                parts = content.get("parts", [])
                if not parts:
                    return "Error: Gemini response content is empty or filtered."
                
                return parts[0].get("text", "Empty text in Gemini response part.").strip()

        except asyncio.TimeoutError:
            logger.error("Request to Gemini API timed out.")
            return "Error: Request to Gemini timed out."
        except aiohttp.ClientError as e:
            logger.error(f"Network or connection error to Gemini: {e}", exc_info=True)
            return f"Error: Network connection failed: {e}"
        except Exception as e:
            logger.error(f"An unexpected error occurred in GeminiClient: {e}", exc_info=True)
            return f"Error: An unexpected error occurred: {e}"


class MistralClient(BaseAiClient):
    """Клиент для Mistral AI API."""
    API_URL = "https://api.mistral.ai/v1/chat/completions"
    
    async def ask_async(self, prompt: str, system_msg: str = "You are a helpful assistant.") -> str:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_msg}, {"role": "user", "content": prompt}]
        }
        logger.debug("Sending request to Mistral API...")
        try:
            session = await self._get_session()
            async with session.post(self.API_URL, headers=headers, json=body, timeout=90) as response:
                logger.info(f"Received response from Mistral with status: {response.status}")
                response_text = await response.text()
                if response.status != 200:
                    logger.error(f"Mistral API Error {response.status}: {response_text}")
                    return f"Mistral Error {response.status}: {response_text[:200]}..."
                data = json.loads(response_text)
                return data.get("choices", [{}])[0].get("message", {}).get("content", "Empty response").strip()
        except asyncio.TimeoutError:
            logger.error("Request to Mistral API timed out.")
            return "Error: Request to Mistral timed out."
        except Exception as e:
            logger.error(f"An unexpected error occurred in MistralClient: {e}", exc_info=True)
            return f"Error: An unexpected error occurred: {e}"


def get_ai_client(provider: str, config: Dict[str, Any]) -> BaseAiClient:
    """Фабричная функция для создания нужного AI клиента."""
    provider = provider.lower()
    
    api_key_env_var = f"{provider.upper()}_API_KEY"
    api_key = os.environ.get(api_key_env_var) or config.get("ai", {}).get("keys", {}).get(provider)
    
    model = config.get("ai", {}).get("models", {}).get(provider)
    
    if not api_key:
        raise ValueError(f"API key for {provider} not found in config or environment variable {api_key_env_var}")
    if not model:
        raise ValueError(f"Model for {provider} not found in config")

    if provider == "openai":
        return OpenAiClient(model=model, api_key=api_key)
    elif provider == "gemini":
        return GeminiClient(model=model, api_key=api_key)
    elif provider == "mistral":
        return MistralClient(model=model, api_key=api_key)
    else:
        raise ValueError(f"Unknown AI provider: {provider}")