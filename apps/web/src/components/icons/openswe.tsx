export function OpenSWELogoSVG({
  className,
  width = 130,
  height = 20,
  style,
}: {
  width?: number;
  height?: number;
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <svg
      width={width}
      height={height}
      viewBox="0 0 1625 250"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      style={style}
      role="img"
      aria-label="Agent Mojo logo"
      preserveAspectRatio="xMidYMid meet"
    >
      <title>Agent Mojo</title>
      <text
        x="20"
        y="50%"
        dominantBaseline="middle"
        fontFamily="'Bebas Neue','Oswald','Impact','Helvetica Neue',Arial,sans-serif"
        fontSize="180"
        fontWeight="800"
        letterSpacing="2"
        fill="currentColor"
      >
        Agent <tspan fontWeight="900">Mojo</tspan>
      </text>
    </svg>
  );
}