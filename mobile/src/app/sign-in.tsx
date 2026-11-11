import { useState } from "react"
import {
  ActivityIndicator,
  Image,
  Keyboard,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  TouchableWithoutFeedback,
  View,
} from "react-native"
import { SafeAreaView } from "react-native-safe-area-context"

import { Theme } from "../design/theme"
import { useAppModel } from "../state/appModel"

export default function SignInScreen() {
  const model = useAppModel()
  const [backend, setBackend] = useState(model.lastBackend)
  const empty = !backend.trim()
  const signIn = () => {
    Keyboard.dismiss()
    model.signIn(backend)
  }
  return (
    <TouchableWithoutFeedback onPress={Keyboard.dismiss} accessible={false}>
      <SafeAreaView style={styles.screen}>
        <View style={styles.hero}>
          <Image
            source={require("../../assets/icon.png")}
            style={styles.mark}
          />
          <Text style={styles.title}>Robin</Text>
          <Text style={styles.subtitle}>
            Your coding agents, in your pocket.
          </Text>
        </View>
        <View style={{ gap: 10, marginBottom: 18 }}>
          <Text style={styles.label}>Backend</Text>
          <TextInput
            value={backend}
            onChangeText={setBackend}
            placeholder="robin.example.com"
            placeholderTextColor={Theme.textTertiary}
            keyboardType="url"
            autoCapitalize="none"
            autoCorrect={false}
            returnKeyType="go"
            onSubmitEditing={signIn}
            style={styles.field}
          />
          {model.signInError ? (
            <Text style={styles.error}>{model.signInError}</Text>
          ) : null}
        </View>
        <Pressable
          onPress={signIn}
          disabled={model.isSigningIn || empty}
          style={({ pressed }) => [
            styles.button,
            { opacity: empty ? 0.5 : pressed ? 0.8 : 1 },
          ]}
        >
          {model.isSigningIn ? (
            <ActivityIndicator color={Theme.onBrand} />
          ) : null}
          <Text style={styles.buttonText}>Continue in Browser</Text>
        </Pressable>
        <Pressable onPress={() => model.startDemo()} style={styles.demo}>
          <Text style={styles.demoText}>Explore a Demo Workspace</Text>
        </Pressable>
      </SafeAreaView>
    </TouchableWithoutFeedback>
  )
}

const styles = StyleSheet.create({
  screen: { flex: 1, paddingHorizontal: 24, backgroundColor: Theme.background },
  hero: { flex: 1, alignItems: "center", justifyContent: "center", gap: 14 },
  mark: { width: 76, height: 76, borderRadius: 17 },
  title: { fontSize: 30, fontWeight: "600", color: Theme.textPrimary },
  subtitle: { fontSize: 17, color: Theme.textSecondary },
  label: { fontSize: 13, color: Theme.textSecondary, marginLeft: 6 },
  field: {
    height: 54,
    paddingHorizontal: 18,
    fontSize: 17,
    color: Theme.textPrimary,
    backgroundColor: Theme.surface,
    borderColor: Theme.stroke,
    borderWidth: StyleSheet.hairlineWidth * 2,
    borderRadius: 16,
  },
  error: { fontSize: 13, color: Theme.error, marginHorizontal: 6 },
  button: {
    height: 56,
    borderRadius: 28,
    backgroundColor: Theme.brand,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
  },
  buttonText: { fontSize: 17, fontWeight: "600", color: Theme.onBrand },
  demo: { alignItems: "center", paddingTop: 18, paddingBottom: 8 },
  demoText: { fontSize: 15, color: Theme.textSecondary },
})
