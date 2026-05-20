import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { NavigationContainer, DefaultTheme } from '@react-navigation/native';
import { StatusBar } from 'expo-status-bar';
import React from 'react';
import { Text } from 'react-native';
import { colors } from './constants/colors';
import { CaptureScreen } from './src/screens/CaptureScreen';
import { LibraryScreen } from './src/screens/LibraryScreen';
import { SettingsScreen } from './src/screens/SettingsScreen';

const Tab = createBottomTabNavigator();

const NAV_THEME = {
  ...DefaultTheme,
  colors: { ...DefaultTheme.colors, background: colors.bg, card: colors.bg2, border: colors.hairline, text: colors.ink0, primary: colors.amber },
};

function icon(label: string, focused: boolean) {
  const map: Record<string, [string, string]> = {
    Capture: ['🎙', '🎙'],
    Library: ['📋', '📋'],
    Settings: ['⚙️', '⚙️'],
  };
  const [active, inactive] = map[label] ?? ['●', '○'];
  return <Text style={{ fontSize: 20, opacity: focused ? 1 : 0.4 }}>{focused ? active : inactive}</Text>;
}

export default function App() {
  return (
    <NavigationContainer theme={NAV_THEME}>
      <StatusBar style="light" />
      <Tab.Navigator
        screenOptions={({ route }) => ({
          tabBarIcon: ({ focused }) => icon(route.name, focused),
          tabBarActiveTintColor: colors.amber,
          tabBarInactiveTintColor: colors.ink3,
          tabBarStyle: { backgroundColor: colors.bg2, borderTopColor: colors.hairline },
          headerShown: false,
        })}
      >
        <Tab.Screen name="Capture"  component={CaptureScreen} />
        <Tab.Screen name="Library"  component={LibraryScreen} />
        <Tab.Screen name="Settings" component={SettingsScreen} />
      </Tab.Navigator>
    </NavigationContainer>
  );
}
