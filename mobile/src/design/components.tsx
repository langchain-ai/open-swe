import { SymbolView, type SFSymbol } from "expo-symbols"
import { useEffect, useState, type ReactNode } from "react"
import {
  Animated,
  Easing,
  StyleSheet,
  Text,
  View,
  type ColorValue,
  type StyleProp,
  type ViewStyle,
} from "react-native"

import { Theme } from "./theme"

/** A rounded, faintly outlined card. */
export function Card({
  children,
  radius = Theme.cornerRadius,
  style,
}: {
  children: ReactNode
  radius?: number
  style?: StyleProp<ViewStyle>
}) {
  return (
    <View style={[styles.card, { borderRadius: radius }, style]}>
      {children}
    </View>
  )
}

export function Separator({ inset = 0 }: { inset?: number }) {
  return <View style={[styles.separator, { marginLeft: inset }]} />
}

export function Symbol({
  name,
  size = 17,
  color = Theme.textSecondary,
  weight,
}: {
  name: SFSymbol
  size?: number
  color?: ColorValue
  weight?: "regular" | "medium" | "semibold" | "bold"
}) {
  return (
    <SymbolView
      name={name}
      size={size}
      tintColor={color}
      weight={weight}
      style={{ width: size, height: size }}
    />
  )
}

/** `+12 -1` in green and red. */
export function ChangeCounts({
  additions,
  deletions,
  size = 15,
}: {
  additions: number
  deletions: number
  size?: number
}) {
  return (
    <Text style={{ fontSize: size, fontVariant: ["tabular-nums"] }}>
      <Text style={{ color: Theme.success }}>+{additions}</Text>
      <Text> </Text>
      <Text style={{ color: Theme.error }}>-{deletions}</Text>
    </Text>
  )
}

const CODE_EXTENSIONS = [
  "py",
  "swift",
  "ts",
  "tsx",
  "js",
  "jsx",
  "go",
  "rs",
  "rb",
  "java",
  "kt",
  "c",
  "cpp",
  "h",
]

/** The icon for a file in a change list. */
export function FileIcon({ path }: { path: string }) {
  const extension = path.split(".").pop()?.toLowerCase() ?? ""
  let name: SFSymbol = "doc"
  if (CODE_EXTENSIONS.includes(extension))
    name = "chevron.left.forwardslash.chevron.right"
  else if (["md", "txt", "rst"].includes(extension)) name = "doc.text"
  else if (["json", "yaml", "yml", "toml"].includes(extension))
    name = "curlybraces"
  else if (["png", "jpg", "jpeg", "gif", "svg"].includes(extension))
    name = "photo"
  return (
    <View style={{ width: 24, alignItems: "center" }}>
      <Symbol name={name} size={17} />
    </View>
  )
}

/** A value that cycles 0 → 1 → 0, for pulsing indicators. */
export function usePulse(duration = 900): Animated.Value {
  const [value] = useState(() => new Animated.Value(0))
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(value, {
          toValue: 1,
          duration,
          easing: Easing.inOut(Easing.sin),
          useNativeDriver: true,
        }),
        Animated.timing(value, {
          toValue: 0,
          duration,
          easing: Easing.inOut(Easing.sin),
          useNativeDriver: true,
        }),
      ])
    )
    loop.start()
    return () => loop.stop()
  }, [value, duration])
  return value
}

/** Four small arrowheads, the "all agents" mark. */
export function AllAgentsGlyph() {
  const arrow = (degrees: number) => (
    <View style={{ transform: [{ rotate: `${degrees}deg` }] }}>
      <Symbol
        name="location.north"
        size={11}
        color={Theme.brand}
        weight="bold"
      />
    </View>
  )
  return (
    <View style={{ gap: 2 }}>
      <View style={{ flexDirection: "row", gap: 2 }}>
        {arrow(-20)}
        {arrow(20)}
      </View>
      <View style={{ flexDirection: "row", gap: 2 }}>
        {arrow(-160)}
        {arrow(160)}
      </View>
    </View>
  )
}

/** A cross of dots that pulses while agents are working. */
export function WorkingGlyph() {
  const pulse = usePulse(700)
  const offsets: [number, number][] = [
    [0, -9],
    [-9, 0],
    [0, 0],
    [9, 0],
    [0, 9],
  ]
  return (
    <View style={{ width: 26, height: 26 }}>
      {offsets.map(([x, y], index) => (
        <Animated.View
          key={index}
          style={{
            position: "absolute",
            left: 13 + x - 3.5,
            top: 13 + y - 3.5,
            width: 7,
            height: 7,
            borderRadius: 3.5,
            backgroundColor: Theme.brand,
            opacity: pulse.interpolate({
              inputRange: [0, 1],
              outputRange: index % 2 === 0 ? [1, 0.45] : [0.45, 1],
            }),
          }}
        />
      ))}
    </View>
  )
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: Theme.surface,
    borderWidth: StyleSheet.hairlineWidth * 2,
    borderColor: Theme.stroke,
    overflow: "hidden",
  },
  separator: {
    height: StyleSheet.hairlineWidth,
    backgroundColor: Theme.separator,
  },
})
