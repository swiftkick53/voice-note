import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { colors } from '../../constants/colors';

interface Props {
  filename: string;
  onPress?: () => void;
}

export function NoteCard({ filename, onPress }: Props) {
  // Parse "2026-05-20 14-30 My Note Title.md" → title + date
  const noExt = filename.replace(/\.md$/, '');
  const match = noExt.match(/^(\d{4}-\d{2}-\d{2}) (\d{2}-\d{2}) (.+)$/);
  const date  = match ? match[1] : '';
  const time  = match ? match[2].replace('-', ':') : '';
  const title = match ? match[3] : noExt;

  return (
    <Pressable style={styles.card} onPress={onPress}>
      <Text style={styles.title} numberOfLines={2}>{title}</Text>
      <Text style={styles.meta}>{date}  {time}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card:  { backgroundColor: colors.surface, borderRadius: 12, borderWidth: 1, borderColor: colors.hairline, padding: 14, gap: 6 },
  title: { color: colors.ink0, fontSize: 14, fontWeight: '500' },
  meta:  { color: colors.ink2, fontSize: 11 },
});
