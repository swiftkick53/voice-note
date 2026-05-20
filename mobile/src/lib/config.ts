import AsyncStorage from '@react-native-async-storage/async-storage';

export interface AppConfig {
  openaiApiKey: string;      // for Whisper transcription
  anthropicApiKey: string;   // for Claude processing
  defaultEngine: string;
  vaultName: string;         // Obsidian vault folder name
}

const DEFAULTS: AppConfig = {
  openaiApiKey: '',
  anthropicApiKey: '',
  defaultEngine: 'clean_format',
  vaultName: 'Claude Brain',
};

const KEY = 'voice_notes_config';

export async function loadConfig(): Promise<AppConfig> {
  try {
    const raw = await AsyncStorage.getItem(KEY);
    if (raw) return { ...DEFAULTS, ...JSON.parse(raw) };
  } catch {}
  return { ...DEFAULTS };
}

export async function saveConfig(config: Partial<AppConfig>): Promise<void> {
  const existing = await loadConfig();
  await AsyncStorage.setItem(KEY, JSON.stringify({ ...existing, ...config }));
}
