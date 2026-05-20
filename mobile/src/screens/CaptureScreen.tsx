import { Audio } from 'expo-av';
import React, { useEffect, useRef, useState } from 'react';
import { Alert, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { colors } from '../../constants/colors';
import { ENGINES } from '../../constants/engines';
import { EngineDrawer } from '../components/EngineDrawer';
import { RecordButton } from '../components/RecordButton';
import { processWithClaude } from '../lib/claude';
import { loadConfig } from '../lib/config';
import { saveNote } from '../lib/storage';
import { transcribeAudio } from '../lib/whisper';

type Status = 'idle' | 'recording' | 'transcribing' | 'processing' | 'saved' | 'error';

const STATUS_LABELS: Record<Status, string> = {
  idle:         'Ready',
  recording:    'Recording…',
  transcribing: 'Transcribing…',
  processing:   'Processing…',
  saved:        'Saved ✓',
  error:        'Something went wrong',
};

export function CaptureScreen() {
  const [status, setStatus]         = useState<Status>('idle');
  const [engine, setEngine]         = useState('clean_format');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [savedTitle, setSavedTitle] = useState('');
  const recordingRef = useRef<Audio.Recording | null>(null);
  const startTimeRef = useRef<number>(0);

  useEffect(() => {
    loadConfig().then(c => { if (c.defaultEngine) setEngine(c.defaultEngine); });
    Audio.requestPermissionsAsync();
  }, []);

  async function toggleRecord() {
    if (status === 'recording') {
      await stopRecording();
    } else if (status === 'idle' || status === 'saved' || status === 'error') {
      await startRecording();
    }
  }

  async function startRecording() {
    try {
      await Audio.setAudioModeAsync({ allowsRecordingIOS: true, playsInSilentModeIOS: true });
      const { recording } = await Audio.Recording.createAsync(
        Audio.RecordingOptionsPresets.HIGH_QUALITY,
      );
      recordingRef.current = recording;
      startTimeRef.current = Date.now();
      setStatus('recording');
      setTranscript('');
      setSavedTitle('');
    } catch (e: any) {
      Alert.alert('Microphone error', e.message);
    }
  }

  async function stopRecording() {
    const recording = recordingRef.current;
    if (!recording) return;
    setStatus('transcribing');
    try {
      await recording.stopAndUnloadAsync();
      const uri = recording.getURI()!;
      const duration = Math.round((Date.now() - startTimeRef.current) / 1000);
      recordingRef.current = null;

      const text = await transcribeAudio(uri);
      setTranscript(text);
      setStatus('processing');

      const processed = await processWithClaude(text, engine);
      const filename = await saveNote(processed, undefined, duration);
      setSavedTitle(filename.replace(/\.md$/, ''));
      setStatus('saved');
    } catch (e: any) {
      setStatus('error');
      Alert.alert('Error', e.message);
    }
  }

  const engineObj = ENGINES.find(e => e.id === engine) ?? ENGINES[0];
  const busy = status === 'transcribing' || status === 'processing';

  return (
    <View style={styles.container}>
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.appTitle}>Voice Notes</Text>
        <TouchableOpacity style={styles.engineChip} onPress={() => setDrawerOpen(true)}>
          <Text style={[styles.engineGlyph, { color: engineObj.color }]}>{engineObj.glyph}</Text>
          <Text style={styles.engineName}>{engineObj.name}</Text>
        </TouchableOpacity>
      </View>

      {/* Main area */}
      <View style={styles.main}>
        <Text style={styles.status}>{STATUS_LABELS[status]}</Text>

        <RecordButton recording={status === 'recording'} onPress={toggleRecord} disabled={busy} />

        {savedTitle ? (
          <Text style={styles.savedNote}>{savedTitle}</Text>
        ) : null}

        {transcript && status !== 'recording' ? (
          <ScrollView style={styles.transcriptBox}>
            <Text style={styles.transcriptText}>{transcript}</Text>
          </ScrollView>
        ) : null}

        {status === 'idle' && (
          <Text style={styles.hint}>Tap to record</Text>
        )}
      </View>

      <EngineDrawer
        visible={drawerOpen}
        selected={engine}
        onSelect={setEngine}
        onClose={() => setDrawerOpen(false)}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container:      { flex: 1, backgroundColor: colors.bg },
  header:         { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 20, paddingTop: 16, paddingBottom: 12, borderBottomWidth: 1, borderColor: colors.hairline },
  appTitle:       { color: colors.ink0, fontSize: 17, fontWeight: '600' },
  engineChip:     { flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: colors.surface, borderRadius: 999, paddingVertical: 6, paddingHorizontal: 12, borderWidth: 1, borderColor: colors.hairline },
  engineGlyph:    { fontSize: 14 },
  engineName:     { color: colors.ink1, fontSize: 12, fontWeight: '500' },
  main:           { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24, gap: 20 },
  status:         { color: colors.ink2, fontSize: 14, letterSpacing: 0.3 },
  hint:           { color: colors.ink3, fontSize: 13 },
  savedNote:      { color: colors.sage, fontSize: 13, textAlign: 'center' },
  transcriptBox:  { maxHeight: 160, width: '100%', backgroundColor: colors.surface, borderRadius: 12, padding: 14, borderWidth: 1, borderColor: colors.hairline },
  transcriptText: { color: colors.ink2, fontSize: 13, lineHeight: 20 },
});
