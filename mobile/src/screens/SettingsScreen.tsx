import React, { useEffect, useState } from 'react';
import { Alert, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { colors } from '../../constants/colors';
import { AppConfig, loadConfig, saveConfig } from '../lib/config';

export function SettingsScreen() {
  const [config, setConfig] = useState<AppConfig>({
    openaiApiKey: '',
    anthropicApiKey: '',
    defaultEngine: 'clean_format',
    vaultName: 'Claude Brain',
  });

  useEffect(() => { loadConfig().then(setConfig); }, []);

  async function save() {
    await saveConfig(config);
    Alert.alert('Saved', 'Settings updated.');
  }

  function field(label: string, key: keyof AppConfig, placeholder: string, secure = false) {
    return (
      <View style={styles.field}>
        <Text style={styles.label}>{label}</Text>
        <TextInput
          style={styles.input}
          value={config[key] as string}
          onChangeText={v => setConfig(c => ({ ...c, [key]: v }))}
          placeholder={placeholder}
          placeholderTextColor={colors.ink3}
          secureTextEntry={secure}
          autoCapitalize="none"
          autoCorrect={false}
        />
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.heading}>Settings</Text>

      <Text style={styles.section}>API Keys</Text>
      {field('OpenAI API Key', 'openaiApiKey', 'sk-...  (for Whisper transcription)', true)}
      {field('Anthropic API Key', 'anthropicApiKey', 'sk-ant-...  (for Claude processing)', true)}

      <Text style={styles.section}>Vault</Text>
      {field('Obsidian Vault Name', 'vaultName', 'Claude Brain')}
      <Text style={styles.hint}>Must match your vault folder name inside iCloud Drive → Obsidian</Text>

      <TouchableOpacity style={styles.saveBtn} onPress={save}>
        <Text style={styles.saveBtnText}>Save Settings</Text>
      </TouchableOpacity>

      <Text style={styles.footer}>Voice Notes v1.0  ·  Get API keys at console.anthropic.com</Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container:   { flex: 1, backgroundColor: colors.bg },
  content:     { padding: 20, gap: 4 },
  heading:     { color: colors.ink0, fontSize: 22, fontWeight: '700', marginBottom: 20 },
  section:     { color: colors.ink2, fontSize: 12, fontWeight: '600', letterSpacing: 0.8, textTransform: 'uppercase', marginTop: 20, marginBottom: 8 },
  field:       { gap: 6, marginBottom: 12 },
  label:       { color: colors.ink1, fontSize: 14 },
  input:       { backgroundColor: colors.surface, borderRadius: 10, borderWidth: 1, borderColor: colors.hairline, color: colors.ink0, fontSize: 14, padding: 12 },
  hint:        { color: colors.ink3, fontSize: 12, marginTop: -6 },
  saveBtn:     { backgroundColor: colors.amber, borderRadius: 12, padding: 14, alignItems: 'center', marginTop: 24 },
  saveBtnText: { color: '#1a1410', fontSize: 15, fontWeight: '700' },
  footer:      { color: colors.ink3, fontSize: 11, textAlign: 'center', marginTop: 32 },
});
