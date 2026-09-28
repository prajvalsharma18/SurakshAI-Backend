"""Configurable structured LLM provider for grounded welfare recommendations."""

from __future__ import annotations

import json
import os
from urllib import request


class WelfareProviderError(RuntimeError):
    """Raised when the configured recommendation provider is unavailable or invalid."""


class OpenAICompatibleWelfareProvider:
    """Minimal provider for an explicitly configured OpenAI-compatible endpoint."""

    def __init__(self, base_url=None, model=None, api_key=None, timeout=30):
        self.base_url = base_url or os.getenv('SURAKSHAI_LLM_BASE_URL')
        self.model = model or os.getenv('SURAKSHAI_LLM_MODEL')
        self.api_key = api_key or os.getenv('SURAKSHAI_LLM_API_KEY')
        self.timeout = timeout

    def generate(self, *, risk_category, explanation, retrieved_context, query):
        if not self.base_url or not self.model or not self.api_key:
            raise WelfareProviderError('No SURAKSHAI LLM provider is configured')
        context = '\n\n'.join(
            f"SOURCE_ID: {item['id']}\nTITLE: {item.get('title')}\nTEXT: {item['text']}"
            for item in retrieved_context
        )
        prompt = (
            'You are a welfare-support recommendation synthesizer. Use only the supplied knowledge. '
            'Do not diagnose, determine fitness, invent policies or contacts, recommend discipline, '
            'change the supplied risk category, or make unsupported causal claims. Return JSON only with '
            'a recommendations array. Each item must contain exactly these fields: '
            'category, action, priority, rationale, and sources. '
            'category must be one of WORKLOAD, RECOVERY, SLEEP, DUTY_SCHEDULING, LEAVE, '
            'SOCIAL_SUPPORT, REFERRAL, or GENERAL. '
            'priority must be one of LOW, MEDIUM, or HIGH. '
            'sources must be an array containing one or more supplied SOURCE_ID values. '
            'Every recommendation must cite one or more supplied SOURCE_ID values. '
            f"\nRisk category (fixed): {risk_category}\nSHAP contributors: {json.dumps(explanation)}"
            f"\nRetrieval query: {query}\nKnowledge:\n{context}"
        )
        body = json.dumps({
            'model': self.model,
            'messages': [
                {'role': 'system', 'content': 'Return only validated JSON welfare recommendations.'},
                {'role': 'user', 'content': prompt},
            ],
        }).encode('utf-8')
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.api_key}',
        }
        try:
            response = request.urlopen(request.Request(self.base_url, data=body, headers=headers, method='POST'), timeout=self.timeout)
            payload = json.loads(response.read().decode('utf-8'))
            content = payload['choices'][0]['message']['content']
            if content.startswith('```'):
                content = content.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
            return json.loads(content)
        except Exception as exc:
            raise WelfareProviderError('Configured welfare recommendation provider failed') from exc


def get_welfare_provider():
    return OpenAICompatibleWelfareProvider()
