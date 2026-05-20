import React, { useEffect, useRef } from 'react';
import { Animated, Pressable, StyleSheet, Text, View } from 'react-native';
import { colors } from '../../constants/colors';

interface Props {
  recording: boolean;
  onPress: () => void;
  disabled?: boolean;
}

export function RecordButton({ recording, onPress, disabled }: Props) {
  const pulse = useRef(new Animated.Value(1)).current;
  const glow = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (recording) {
      Animated.loop(
        Animated.sequence([
          Animated.timing(pulse, { toValue: 1.12, duration: 600, useNativeDriver: true }),
          Animated.timing(pulse, { toValue: 1.0,  duration: 600, useNativeDriver: true }),
        ])
      ).start();
      Animated.timing(glow, { toValue: 1, duration: 300, useNativeDriver: false }).start();
    } else {
      pulse.stopAnimation();
      Animated.timing(pulse, { toValue: 1, duration: 200, useNativeDriver: true }).start();
      Animated.timing(glow, { toValue: 0, duration: 300, useNativeDriver: false }).start();
    }
  }, [recording]);

  const borderColor = glow.interpolate({
    inputRange: [0, 1],
    outputRange: [colors.amber + '40', colors.coral + 'cc'],
  });

  return (
    <Pressable onPress={onPress} disabled={disabled} style={styles.hitArea}>
      <Animated.View style={[styles.ring, { borderColor }]}>
        <Animated.View style={[styles.btn, { transform: [{ scale: pulse }] }]}>
          <View style={[styles.inner, recording && styles.innerActive]}>
            {recording
              ? <View style={styles.stopSquare} />
              : <Text style={styles.mic}>🎙</Text>
            }
          </View>
        </Animated.View>
      </Animated.View>
    </Pressable>
  );
}

const SIZE = 120;
const styles = StyleSheet.create({
  hitArea:     { padding: 16 },
  ring:        { width: SIZE + 24, height: SIZE + 24, borderRadius: (SIZE + 24) / 2, borderWidth: 2, alignItems: 'center', justifyContent: 'center' },
  btn:         { width: SIZE, height: SIZE, borderRadius: SIZE / 2, overflow: 'hidden' },
  inner:       { flex: 1, borderRadius: SIZE / 2, backgroundColor: colors.surface, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: colors.hairline },
  innerActive: { backgroundColor: colors.coral + '22' },
  stopSquare:  { width: 32, height: 32, borderRadius: 6, backgroundColor: colors.coral },
  mic:         { fontSize: 40 },
});
