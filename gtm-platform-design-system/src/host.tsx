"use client";

/*
 * The host adapter: the one place this package learns about its application.
 *
 * Three slots, and no more. A component that needs a fourth is asking the
 * design system to know something about the product, which is the boundary this
 * module exists to hold. Every slot has a working default, so a consumer that
 * renders no provider still gets a functioning system with plain anchors,
 * plain images and a theme on `data-theme`.
 */

import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ComponentType,
  type ReactNode,
} from "react";

type ThemeName = "light" | "dark" | "system";

interface LinkSlotProps {
  href: string;
  children?: ReactNode;
  className?: string;
  onClick?: (event: React.MouseEvent<HTMLElement>) => void;
  target?: string;
  rel?: string;
}

/*
 * The image slot carries `next/image`'s vocabulary because the components were
 * written against it. A consumer who supplies no image component gets a plain
 * `<img>`, and `HostImage` is what translates: `fill` becomes the absolute
 * box it means in Next, and the optimizer hint is dropped rather than leaked
 * onto the DOM as an unknown attribute.
 */
interface ImageSlotProps {
  src: string;
  alt: string;
  width?: number;
  height?: number;
  className?: string;
  onError?: () => void;
  /** Fills the nearest positioned ancestor instead of taking an intrinsic box. */
  fill?: boolean;
  /** Next's optimizer hint. Meaningless to a plain `<img>`, which drops it. */
  unoptimized?: boolean;
  draggable?: boolean;
  referrerPolicy?: string;
  "data-slot"?: string;
}

interface HostSlots {
  /** The router's link. `next/link`, TanStack Router's `Link`, or omitted for `<a>`. */
  link?: ComponentType<LinkSlotProps>;
  /** An image component. `next/image`, or omitted for `<img>`. */
  image?: ComponentType<ImageSlotProps>;
  /** Theme state. Omitted means this package owns `data-theme` on `<html>`. */
  theme?: { theme?: ThemeName; setTheme: (next: ThemeName) => void };
  /** The product's own mark, where a pattern reserves a lane for one. */
  brandMark?: ComponentType<{ className?: string }>;
}

const HostContext = createContext<HostSlots>({});

const THEME_STORAGE_KEY = "gtm-design-theme";

/*
 * The fallback theme. It writes the same `data-theme` attribute the token sheet
 * keys on, so a consumer who already drives that attribute can ignore this
 * entirely and a consumer who has nothing gets a working toggle.
 */
function useOwnedTheme(enabled: boolean): {
  theme?: ThemeName;
  setTheme: (next: ThemeName) => void;
} {
  const [theme, setThemeState] = useState<ThemeName>("system");

  useEffect(() => {
    if (!enabled) return;
    const stored = globalThis.localStorage?.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark" || stored === "system") {
      setThemeState(stored);
    }
  }, [enabled]);

  useEffect(() => {
    /* A host that supplies `theme` owns the DOM; this fallback must not race it. */
    if (!enabled) return;
    const root = globalThis.document?.documentElement;
    if (root === undefined) return;
    const dark =
      theme === "dark" ||
      (theme === "system" &&
        globalThis.matchMedia?.("(prefers-color-scheme: dark)").matches === true);
    root.setAttribute("data-theme", dark ? "dark" : "light");
    /* Consumers on shadcn's class-based dark variant read this instead. */
    root.classList.toggle("dark", dark);
  }, [enabled, theme]);

  const setTheme = useCallback((next: ThemeName) => {
    setThemeState(next);
    globalThis.localStorage?.setItem(THEME_STORAGE_KEY, next);
  }, []);

  return { theme, setTheme };
}

export function useTheme(): {
  theme?: ThemeName;
  setTheme: (next: ThemeName) => void;
} {
  const host = useContext(HostContext);
  const owned = useOwnedTheme(host.theme === undefined);
  return host.theme ?? owned;
}

export function HostLink({
  href,
  children,
  ...rest
}: LinkSlotProps): React.ReactElement {
  const { link } = useContext(HostContext);
  if (link !== undefined) {
    return createElement(link, { href, ...rest }, children);
  }
  return createElement("a", { href, ...rest }, children);
}

const FILL_CLASS = "absolute inset-0 size-full";

export function HostImage({
  src,
  alt,
  fill,
  unoptimized: _unoptimized,
  className,
  width,
  height,
  ...rest
}: ImageSlotProps): React.ReactElement {
  const { image } = useContext(HostContext);
  if (image !== undefined) {
    return createElement(image, {
      src,
      alt,
      fill,
      unoptimized: _unoptimized,
      className,
      width,
      height,
      ...rest,
    });
  }
  if (fill === true) {
    return createElement("img", {
      src,
      alt,
      className: className === undefined ? FILL_CLASS : `${FILL_CLASS} ${className}`,
      ...rest,
    });
  }
  return createElement("img", { src, alt, className, width, height, ...rest });
}

/*
 * Renders nothing when the consumer set no mark. A rail with an empty brand
 * lane is correct; a rail wearing someone else's logo is not.
 */
export function HostBrandMark({
  className,
}: {
  className?: string;
}): React.ReactElement | null {
  const { brandMark } = useContext(HostContext);
  if (brandMark === undefined) return null;
  return createElement(brandMark, { className });
}

export function DesignSystemProvider({
  children,
  ...slots
}: HostSlots & { children: ReactNode }): React.ReactElement {
  const value = useMemo(
    () => ({
      link: slots.link,
      image: slots.image,
      theme: slots.theme,
      brandMark: slots.brandMark,
    }),
    [slots.link, slots.image, slots.theme, slots.brandMark],
  );
  return createElement(HostContext.Provider, { value }, children);
}

export type { HostSlots, LinkSlotProps, ImageSlotProps, ThemeName };
