import type { IconComponent } from "@langchain/macaw-components/utils/icon-types"

/** Robin's bird in its light blue, traced from the app icon, sized like a Phosphor icon. */
export const RobinIcon: IconComponent = ({
  className,
  size = "1em",
  mirrored: _mirrored,
  weight: _weight,
  ...props
}) => (
  <svg
    viewBox="0 0 48 40"
    width={size}
    height={size}
    fill="currentColor"
    {...props}
    className={className}
    style={{
      color: "light-dark(var(--icon-brand-fill), var(--icon-brand-background))",
    }}
  >
    <path
      fillRule="evenodd"
      d="M34.2 0.1L33.1 0.2L32.1 0.4L30.2 1.1L28.3 2.1L27.4 2.8L26.5 3.6L25.2 5.1L22.7 8.4L21.6 9.8L20.3 11L17.2 13.8L14.8 16.3L12.6 19.2L9.6 23.8L8.2 25.5L5.9 27.8L0.6 32.6L0.3 32.9L0.2 33.4L0.1 33.9L0.2 34.3L0.4 34.7L0.7 35.1L1.4 35.6L2.3 35.8L3.1 35.7L9.9 33.7L12.2 33.1L14 34.8L15.6 36L17.2 37L19 38L20.6 38.6L22.1 39L24.1 39.5L25.7 39.6L27.5 39.7L29.1 39.6L30.7 39.4L32.5 38.9L34.3 38.3L35.3 37.9L36.9 37L38.3 36.1L39.6 35.1L40.7 34L41.6 33L42.7 31.6L43.5 30.3L44.3 28.7L44.9 27.1L45.4 25.5L45.7 23.8L45.8 22.4L45.8 21.3L45.6 19.3L45.2 17.6L44.9 16.4L43.1 12L43 11L43 10.2L43.2 9.4L43.4 9.1L47.6 7.1L47.8 6.9L47.8 6.7L47.6 6.5L43.3 5L42.3 3.7L41.3 2.6L40.2 1.8L39 1.1L37.9 0.6L36.8 0.3L35.4 0.2L34.2 0.1ZM35.7 6.7L35.8 7.3L36.2 7.9L36.8 8.3L37.5 8.5L38.2 8.3L38.7 7.9L39.1 7.3L39.3 6.7L39.1 6L38.7 5.4L38.2 5L37.5 4.9L36.8 5L36.2 5.4L35.8 6L35.7 6.7ZM40 12L38.6 12.3L37.1 12.9L35.6 14L34.4 15.1L33.4 16.4L32.2 18.3L31.4 19.9L29.6 24.6L28.8 26.1L28 27.3L27 28.5L25.7 29.7L24.5 30.6L22.6 31.8L23.2 32.3L24.7 33.2L25.9 33.8L27.5 34.3L28.6 34.5L30 34.6L31.6 34.6L33.1 34.4L34.6 34L36.2 33.4L37.7 32.5L39.3 31.4L40.7 30L41.8 28.4L42.5 27.1L43.2 25.3L43.7 23.1L43.9 21.4L43.8 19.1L43.4 16.7L42.8 14.7L41.7 12.2L41.6 12.1L41.1 12L40 12Z"
    />
  </svg>
)
