import * as FileSystem from 'expo-file-system';
import { loadConfig } from './config';

function formatDate(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}-${pad(d.getMinutes())}`;
}

/**
 * Save a processed note to iCloud Drive (Obsidian vault) or local Documents.
 * Filename: "YYYY-MM-DD HH-mm <title>.md"
 */
export async function saveNote(
  content: string,
  title?: string,
  durationSecs?: number,
): Promise<string> {
  const config = await loadConfig();
  const now = new Date();
  const dateStr = formatDate(now);
  const safeTitle = (title || 'Voice Note')
    .replace(/[^a-zA-Z0-9 \-_]/g, '')
    .trim()
    .slice(0, 60);
  const filename = `${dateStr} ${safeTitle}.md`;

  const frontmatter = [
    '---',
    `date: ${now.toISOString()}`,
    `duration: ${durationSecs ?? 0}`,
    'tags: [voice-note]',
    '---',
    '',
  ].join('\n');

  const fullContent = frontmatter + content;

  // Try iCloud Drive path first
  // iOS iCloud path: /private/var/mobile/Library/Mobile Documents/iCloud~md~obsidian/Documents/<vault>/Voice Notes/
  const icloudBase = `${FileSystem.documentDirectory}../../Library/Mobile Documents/iCloud~md~obsidian/Documents/`;
  const vaultPath = `${icloudBase}${config.vaultName}/Voice Notes/`;

  let savePath: string;

  try {
    const dirInfo = await FileSystem.getInfoAsync(vaultPath);
    if (dirInfo.exists) {
      savePath = vaultPath + filename;
    } else {
      // iCloud not available — fall back to local Documents
      const localPath = `${FileSystem.documentDirectory}Voice Notes/`;
      await FileSystem.makeDirectoryAsync(localPath, { intermediates: true });
      savePath = localPath + filename;
    }
  } catch {
    const localPath = `${FileSystem.documentDirectory}Voice Notes/`;
    await FileSystem.makeDirectoryAsync(localPath, { intermediates: true });
    savePath = localPath + filename;
  }

  await FileSystem.writeAsStringAsync(savePath, fullContent, {
    encoding: FileSystem.EncodingType.UTF8,
  });

  return filename;
}

/** List recent notes from the vault or local fallback. */
export async function listRecentNotes(limit = 20): Promise<{ filename: string; path: string }[]> {
  const config = await loadConfig();
  const icloudBase = `${FileSystem.documentDirectory}../../Library/Mobile Documents/iCloud~md~obsidian/Documents/`;
  const vaultPath = `${icloudBase}${config.vaultName}/Voice Notes/`;
  const localPath = `${FileSystem.documentDirectory}Voice Notes/`;

  let dirPath = localPath;
  try {
    const info = await FileSystem.getInfoAsync(vaultPath);
    if (info.exists) dirPath = vaultPath;
  } catch {}

  try {
    const files = await FileSystem.readDirectoryAsync(dirPath);
    return files
      .filter(f => f.endsWith('.md'))
      .sort()
      .reverse()
      .slice(0, limit)
      .map(f => ({ filename: f, path: dirPath + f }));
  } catch {
    return [];
  }
}
