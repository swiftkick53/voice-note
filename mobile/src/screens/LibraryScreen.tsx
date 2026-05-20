import * as FileSystem from 'expo-file-system';
import React, { useCallback, useState } from 'react';
import { Alert, FlatList, RefreshControl, StyleSheet, Text, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { colors } from '../../constants/colors';
import { NoteCard } from '../components/NoteCard';
import { listRecentNotes } from '../lib/storage';

export function LibraryScreen() {
  const [notes, setNotes]       = useState<{ filename: string; path: string }[]>([]);
  const [refreshing, setRefreshing] = useState(false);

  async function load() {
    setRefreshing(true);
    const items = await listRecentNotes(30);
    setNotes(items);
    setRefreshing(false);
  }

  useFocusEffect(useCallback(() => { load(); }, []));

  async function openNote(path: string, filename: string) {
    try {
      const content = await FileSystem.readAsStringAsync(path);
      Alert.alert(filename.replace(/\.md$/, ''), content.slice(0, 600) + (content.length > 600 ? '…' : ''));
    } catch {
      Alert.alert('Could not open note');
    }
  }

  return (
    <View style={styles.container}>
      <Text style={styles.heading}>Library</Text>
      {notes.length === 0 ? (
        <View style={styles.empty}>
          <Text style={styles.emptyIcon}>🎙</Text>
          <Text style={styles.emptyText}>No notes yet — record one to begin.</Text>
        </View>
      ) : (
        <FlatList
          data={notes}
          keyExtractor={item => item.filename}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={load} tintColor={colors.ink2} />}
          renderItem={({ item }) => (
            <NoteCard filename={item.filename} onPress={() => openNote(item.path, item.filename)} />
          )}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container:  { flex: 1, backgroundColor: colors.bg },
  heading:    { color: colors.ink0, fontSize: 22, fontWeight: '700', padding: 20, paddingBottom: 12 },
  list:       { padding: 16, gap: 10 },
  empty:      { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12 },
  emptyIcon:  { fontSize: 40, opacity: 0.3 },
  emptyText:  { color: colors.ink3, fontSize: 14 },
});
