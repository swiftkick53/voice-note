import { loadConfig } from './config';
import { ACTION_PROMPTS } from '../../constants/engines';

/**
 * Process a transcript with Claude using the selected engine.
 * @param transcript - raw transcript text
 * @param action - engine id (clean_format, action_items, etc.)
 * @returns processed markdown string
 */
export async function processWithClaude(transcript: string, action: string): Promise<string> {
  const config = await loadConfig();

  // TODO: Add your Anthropic API key in Settings to enable AI processing
  if (!config.anthropicApiKey) {
    throw new Error('Anthropic API key not set. Add it in Settings.');
  }

  if (action === 'raw') {
    return `> [!tldr]\n> Voice note transcript\n\n${transcript}`;
  }

  const template = ACTION_PROMPTS[action] ?? ACTION_PROMPTS['clean_format'];
  const prompt = template.replace('{transcript}', transcript);

  const response = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'x-api-key': config.anthropicApiKey,
      'anthropic-version': '2023-06-01',
      'content-type': 'application/json',
    },
    body: JSON.stringify({
      model: 'claude-sonnet-4-5',
      max_tokens: 2048,
      messages: [{ role: 'user', content: prompt }],
    }),
  });

  if (!response.ok) {
    const err = await response.text();
    throw new Error(`Anthropic API error ${response.status}: ${err}`);
  }

  const data = await response.json();
  return data.content?.[0]?.text?.trim() ?? `> [!tldr]\n> Voice note transcript\n\n${transcript}`;
}
