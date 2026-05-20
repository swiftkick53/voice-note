import { loadConfig } from './config';

/**
 * Transcribe an audio file using OpenAI Whisper API.
 * @param uri - local file URI from expo-av recording
 * @returns transcript string
 */
export async function transcribeAudio(uri: string): Promise<string> {
  const config = await loadConfig();

  // TODO: Add your OpenAI API key in Settings to enable transcription
  if (!config.openaiApiKey) {
    throw new Error('OpenAI API key not set. Add it in Settings.');
  }

  const formData = new FormData();
  formData.append('file', {
    uri,
    name: 'recording.m4a',
    type: 'audio/m4a',
  } as any);
  formData.append('model', 'whisper-1');
  formData.append('language', 'en');

  const response = await fetch('https://api.openai.com/v1/audio/transcriptions', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${config.openaiApiKey}`,
    },
    body: formData,
  });

  if (!response.ok) {
    const err = await response.text();
    throw new Error(`Whisper API error ${response.status}: ${err}`);
  }

  const data = await response.json();
  return data.text?.trim() ?? '';
}
