import React from 'react';
import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';
import { colors } from '../../constants/colors';
import { ENGINES, Engine } from '../../constants/engines';

interface Props {
  visible: boolean;
  selected: string;
  onSelect: (engineId: string) => void;
  onClose: () => void;
}

export function EngineDrawer({ visible, selected, onSelect, onClose }: Props) {
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose} />
      <View style={styles.sheet}>
        <View style={styles.handle} />
        <Text style={styles.title}>Process with</Text>
        <View style={styles.grid}>
          {ENGINES.map((e: Engine) => (
            <Pressable
              key={e.id}
              style={[styles.tile, selected === e.id && styles.tileActive, { borderColor: e.color + '60' }]}
              onPress={() => { onSelect(e.id); onClose(); }}
            >
              <Text style={[styles.glyph, { color: e.color }]}>{e.glyph}</Text>
              <Text style={styles.name}>{e.name}</Text>
              <Text style={styles.hint}>{e.hint}</Text>
            </Pressable>
          ))}
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop:   { flex: 1, backgroundColor: 'rgba(0,0,0,0.5)' },
  sheet:      { backgroundColor: colors.bg2, borderTopLeftRadius: 20, borderTopRightRadius: 20, padding: 20, paddingBottom: 40 },
  handle:     { width: 40, height: 4, borderRadius: 2, backgroundColor: colors.ink3, alignSelf: 'center', marginBottom: 16 },
  title:      { color: colors.ink1, fontSize: 16, fontWeight: '600', marginBottom: 16 },
  grid:       { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  tile:       { width: '48%', backgroundColor: colors.surface, borderRadius: 12, borderWidth: 1, borderColor: colors.hairline, padding: 14, gap: 4 },
  tileActive: { backgroundColor: colors.bg3 },
  glyph:      { fontSize: 20 },
  name:       { color: colors.ink0, fontSize: 13, fontWeight: '600' },
  hint:       { color: colors.ink2, fontSize: 11 },
});
